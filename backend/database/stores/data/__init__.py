"""The data layer's stores — the shared shape, and each domain over it.

    resources.py    ResourceStore, the base every domain extends
    secrets.py      credentials; values write-only forever
    definitions.py  the versioned templates secrets are created from
    records.py      what agents keep for people
    files.py        documents, bytes included
    skills.py       knowledge written for the assistant
    mcp.py          remote tool servers a person added
"""

from database.stores.data.definitions import DefinitionStore
from database.stores.data.files import FileStore
from database.stores.data.records import AgentDataStore
from database.stores.data.resources import ResourceStore
from database.stores.data.secrets import SecretStore
from database.stores.data.mcp import McpServerStore
from database.stores.data.skills import SkillStore

__all__ = [
    "AgentDataStore",
    "DefinitionStore",
    "FileStore",
    "ResourceStore",
    "SecretStore",
    "McpServerStore",
    "SkillStore",
]
