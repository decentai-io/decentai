"""The LLM agent (see manifest.yaml).

Every function is one mediated completion: the platform injects the
chat's model because the manifest declares ``llm: true``, and the agent
holds nothing else — no secrets, no state, no network of its own.
"""

from decentai_sdk.base import AgentBase

from .tools import TextTool


class LLMAgent(AgentBase):
    def tools(self):
        return [TextTool(self)]
