"""The bytes layer. Connectors move bytes; the ``files`` documents are the
record of truth. All speak the same contract (base.py) — writes go to the
configured provider, reads follow each document's storage_provider stamp.
This platform ships one, the local disk; a deployment that keeps its
bytes elsewhere adds a provider as one file here and one line in
``CONNECTORS``.
"""

from database.file_connectors.base import FileConnector
from database.file_connectors.local import LocalFileConnector

# The roster: storage_provider stamp → implementation.
CONNECTORS = {
    LocalFileConnector.storage_provider_type: LocalFileConnector,
}

__all__ = [
    "CONNECTORS",
    "FileConnector",
    "LocalFileConnector",
]
