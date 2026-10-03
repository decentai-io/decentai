"""How the runtime reaches a model.

    factory.py   provider name -> the connector that speaks it
    connector/   one per provider, behind one interface

Two things, and the split is the point: the factory decides WHICH, a
connector decides HOW. A provider that speaks a protocol a connector
here already speaks is an entry in the catalog both sides read
(contracts/llm_providers.py) and no code here; one with a protocol of
its own is a file in connector/ and a line in the factory's table.
Nothing that reasons has to hear about either.

What the whole package refuses to do is read configuration. A connector
is built from a dict the host assembled — the chat's own llm block,
plus the key of the model connection it names, served by the backend
as that person (Settings:Llm:Use) — so which model a chat thinks with
is a decision stored per chat, and revoking a key is removing a
connection rather than redeploying anything.
"""

from ai_runtime.llms.connector import (
    AnthropicConnector,
    BedrockConnector,
    FakeConnector,
    OpenAIConnector,
)
from ai_runtime.llms.factory import LLMConnectorFactory

__all__ = [
    "AnthropicConnector",
    "BedrockConnector",
    "FakeConnector",
    "LLMConnectorFactory",
    "OpenAIConnector",
]
