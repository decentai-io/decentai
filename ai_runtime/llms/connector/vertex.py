"""Google Vertex AI: Gemini, and Claude, behind a Google Cloud project.

The same models as Gemini's own API and Anthropic's, reached through a
project, a region and a service account (contracts/vertex.py) instead
of an API key:

- **Gemini** speaks generateContent exactly as the Gemini API does
  (gemini.py); only the address — the project, the region and the
  model under ``publishers/google`` — and the signature, a Bearer token
  in place of ``x-goog-api-key``, differ. Embeddings are asked of
  ``:predict``, Vertex's own way for an embedding model.
- **Claude** is Anthropic's messages API through Anthropic's own Vertex
  client, handed the account's credentials, which keeps its token fresh
  itself; everything else is the Anthropic connector's (anthropic.py).
"""

from __future__ import annotations

import asyncio
from urllib.parse import quote

import httpx

from ai_runtime.llms.connector.anthropic import AnthropicConnector
from ai_runtime.llms.connector.gemini import GeminiConnector
from ai_runtime.llms.connector.tools import setting
from contracts.vertex import VertexAccount


class VertexGeminiConnector(GeminiConnector):
    NAME = "Vertex AI"

    def __init__(self, config: dict):
        super().__init__(config)
        self.account = VertexAccount(self.api_key, self.endpoint)

    def _url(self, verb: str) -> str:
        return (f"{self.account.base}/publishers/google/models/"
                f"{quote(self.model, safe='')}:{verb}")

    async def _ask(self, verb: str, body: dict) -> dict:
        token = await asyncio.to_thread(self.account.token)
        return await self._post(self._url(verb), body,
                                {"Authorization": f"Bearer {token}"})

    async def embed(self, texts: list) -> list:
        """Vectors from a Vertex embedding model, in order."""
        answer = await self._ask("predict", {
            "instances": [{"content": str(text)} for text in texts]})
        return [list(((item or {}).get("embeddings") or {}).get("values") or [])
                for item in answer.get("predictions") or []]


class VertexAnthropicConnector(AnthropicConnector):
    def __init__(self, config: dict):
        from anthropic import AsyncAnthropicVertex

        key = str(config.get("api_key") or "")
        self.model = str(config.get("model") or "")
        if not key:
            raise ValueError("Vertex AI connector requires api_key")
        if not self.model:
            raise ValueError("Vertex AI connector requires model")
        self._ceiling = None
        self.account = VertexAccount(key, config.get("endpoint"))
        self.client = AsyncAnthropicVertex(
            region=self.account.location,
            project_id=self.account.project,
            credentials=self.account.credentials(),
            timeout=setting(config, "timeout_seconds", 60, 1, 600),
            max_retries=int(setting(config, "max_retries", 2, 0, 10)),
            # As the Anthropic connector: a redirect is not followed, so
            # the token in this client's headers goes nowhere else.
            http_client=httpx.AsyncClient(follow_redirects=False),
        )
