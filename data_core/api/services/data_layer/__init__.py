"""The data layer — the CONTROLLERS over the shared resource shape.

One module per domain, each holding that domain's controller; the
stores they speak for live under ``database/stores`` with everything
else that touches Mongo, and the sharing rules they enforce live in
``server/governance/sharing.py``. The rule of the split: anything that touches
the database lives under ``database/``; anything that reads a gateway
payload lives here.

A secret's values reach no person through the API: they are decrypted
for the runtime acting in a chat, at Secrets:Secret:Use. A record's,
a skill's and a file's values are served to whoever may see them.
"""

from api.services.data_layer.base import ResourceController
from api.services.data_layer.definitions import DefinitionController
from api.services.data_layer.files import FileController
from api.services.data_layer.records import DataController
from api.services.data_layer.secrets import SecretController
from api.services.data_layer.skills import SkillController

__all__ = [
    "DataController",
    "DefinitionController",
    "FileController",
    "ResourceController",
    "SecretController",
    "SkillController",
]
