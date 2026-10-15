"""The bytes layer. Connectors move bytes; the ``files`` documents are the
record of truth. All speak the same contract (base.py) — writes go to the
configured provider, reads follow each document's storage_provider stamp.
The platform ships four: the local disk, and S3, Azure Blob Storage and
Google Cloud Storage (cloud.py, over database/blob_stores.py). Another
is one file here and one line in ``CONNECTORS``.
"""

from database.file_connectors.base import FileConnector
from database.file_connectors.cloud import (
    AzureBlobFileConnector, GcsFileConnector, S3FileConnector)
from database.file_connectors.local import LocalFileConnector

# The roster: storage_provider stamp → implementation.
CONNECTORS = {
    connector.storage_provider_type: connector
    for connector in (LocalFileConnector, S3FileConnector,
                      AzureBlobFileConnector, GcsFileConnector)
}

__all__ = [
    "CONNECTORS",
    "FileConnector",
    "LocalFileConnector",
    "S3FileConnector",
    "AzureBlobFileConnector",
    "GcsFileConnector",
]
