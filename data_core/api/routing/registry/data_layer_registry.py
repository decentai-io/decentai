# The data layer: three sibling gateway domains over one document format.
# Each stays its own domain because endpoint strings are permission strings.

from api.services.data_layer import (
    DataController,
    SkillController,
    DefinitionController,
    FileController,
    SecretController,
)
from api.services.data_layer.credentials import CredentialController
from api.services.data_layer.mcp import McpController
from api.services.data_layer.oauth import OauthConnectController


def build_data_layer_endpoints():
    return {
        "Secrets": {
            "Secret": SecretController(),
            "Definition": DefinitionController(),
            # Connecting an account: the consent flow that ends in a
            # secret, for definitions that carry an oauth block.
            "Oauth": OauthConnectController(),
            # A login an agent asked for as it worked: resolved by the
            # runtime, typed in and allowed by the person.
            "Credential": CredentialController(),
        },
        "Data": {"Record": DataController()},
        "Skills": {"Skill": SkillController()},
        # Remote tool servers a person added for their own chats.
        "Mcp": {"Server": McpController()},
        "Files": {"File": FileController()},
    }
