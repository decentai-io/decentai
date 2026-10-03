"""One connector per provider, and what they all have in common.

    anthropic.py     system prompt is a top-level parameter, not a message
    bedrock.py       Amazon's Converse: blocks, toolSpec, the model in the address
    openai.py        ...and every server that speaks its protocol
    fake.py          scripted, no network — see its own docstring

CONFIG-PURE. A connector is constructed from an explicit config dict
(provider, model, endpoint, api_key, ...) and never reads the
environment: which LLM a chat uses comes from its chat config, and the
key from the model connection it names, read as the turn begins. So
rotating a key or changing a model is a backend operation that the next
turn picks up, and no deployment ever holds an LLM secret in its own
configuration.

ONE INTERFACE. ``async chat(messages, max_tokens, tools) -> ModelReply``
(tools.py): ``content`` is the reply as the cycle reads it, and beside
it the calls, the prose and the stop reason.
Everything a provider does differently is absorbed on this side of it —
Anthropic splitting the system prompt out of the messages array and
OpenAI-compatible servers sharing the chat-completions request format. A reasoning loop knows none of it.

TOOLS, THE SAME WAY. Offered ``tools`` (the assistant's actions as
schemas, reasoning/actions.py), a connector asks the model to call one
and hands the call back as the action's JSON text — the shape the cycle
always read — so native tool calling changes how the model is asked,
not what the runtime does with the answer (tools.py). Anthropic takes
``input_schema`` and ``tool_choice: any``; OpenAI-compatible servers
``required``, or ``auto`` if they refuse it.

AGENTS NEVER TOUCH THESE. Reasoning loops hold connectors; agent code
holds functions. An agent that could reach a model could reason its way
around the executor, which is the one thing every invocation must pass
through.
"""

from ai_runtime.llms.connector.anthropic import AnthropicConnector
from ai_runtime.llms.connector.bedrock import BedrockConnector
from ai_runtime.llms.connector.fake import FakeConnector
from ai_runtime.llms.connector.openai import OpenAIConnector

__all__ = [
    "AnthropicConnector",
    "BedrockConnector",
    "FakeConnector",
    "OpenAIConnector",
]
