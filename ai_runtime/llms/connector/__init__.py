"""One connector per provider, and what they all have in common.

    anthropic.py     system prompt is a top-level parameter, not a message
    bedrock.py       Amazon's Converse: blocks, toolSpec, the model in the address
    gemini.py        Google's generateContent: parts, functionDeclarations
    openai.py        chat completions, and every server that speaks them
    responses.py     OpenAI's Responses: instructions, input, output items
    fake.py          scripted, no network — see its own docstring
    http.py          the POST the two without a client library share

CONFIG-PURE. A connector is constructed from an explicit config dict
(provider, model, endpoint, api_key, ...): which LLM a chat uses comes
from its chat config, and the key from the model connection it names,
read as the turn begins. So rotating a key or changing a model is a
backend operation that the next turn picks up, and no deployment ever
holds an LLM secret in its own configuration.

The code here reads nothing from the environment. What it calls does,
for what the config leaves unsaid: the HTTP client, for the way this
machine reaches the internet (HTTPS_PROXY, a CA bundle); OpenAI's
library, for an organization and a project (OPENAI_ORG_ID,
OPENAI_PROJECT_ID), sent to the connection's address; and OpenAI's and
Anthropic's libraries, for an address when the connection names none
(OPENAI_BASE_URL, ANTHROPIC_BASE_URL).

ONE INTERFACE. ``async chat(messages, max_tokens, tools) -> ModelReply``
(tools.py): ``content`` is the reply as the cycle reads it, and beside
it the calls, the prose and the stop reason.
Everything a provider does differently is absorbed on this side of it —
Anthropic splitting the system prompt out of the messages array,
OpenAI-compatible servers sharing the chat-completions request format,
Gemini and Bedrock naming the model in the address. A reasoning loop
knows none of it.

TOOLS, THE SAME WAY. Offered ``tools`` (the assistant's actions as
schemas, reasoning/actions.py), a connector asks the model to call one
and hands the call back as the action's JSON text — the shape the cycle
always read — so native tool calling changes how the model is asked,
not what the runtime does with the answer (tools.py). Anthropic takes
``input_schema`` and ``tool_choice: any``; OpenAI's two protocols
``required``, Gemini ``ANY`` and Bedrock ``any`` — each asked with its
``auto`` from then on if the server refuses to be made to call.

AGENTS NEVER TOUCH THESE. Reasoning loops hold connectors; agent code
holds functions. An agent that could reach a model could reason its way
around the executor, which is the one thing every invocation must pass
through.
"""

from ai_runtime.llms.connector.anthropic import AnthropicConnector
from ai_runtime.llms.connector.bedrock import BedrockConnector
from ai_runtime.llms.connector.fake import FakeConnector
from ai_runtime.llms.connector.gemini import GeminiConnector
from ai_runtime.llms.connector.openai import OpenAIConnector
from ai_runtime.llms.connector.responses import OpenAIResponsesConnector

__all__ = [
    "AnthropicConnector",
    "BedrockConnector",
    "FakeConnector",
    "GeminiConnector",
    "OpenAIConnector",
    "OpenAIResponsesConnector",
]
