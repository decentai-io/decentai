"""Google Vertex AI: Gemini and Claude behind a project, a region and a service account's key."""

from __future__ import annotations

import asyncio
from typing import Any

from anthropic import AsyncAnthropicVertex

from shared.llm.vertex import VertexAccount

from .anthropic import Anthropic
from .gemini import Gemini


class VertexGemini(Gemini):
    NAME = "Vertex AI"
    URL = "{endpoint}/publishers/google/models/{model}:{verb}"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.account = VertexAccount(self.api_key, self.endpoint)
        self.endpoint = self.account.base

    async def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await asyncio.to_thread(self.account.token)}"}


class VertexAnthropic(Anthropic):
    NAME = "Vertex AI"

    def _client(self) -> AsyncAnthropicVertex:
        account = VertexAccount(self.api_key, self.endpoint)
        return AsyncAnthropicVertex(region=account.location, project_id=account.project,
                                    credentials=account.credentials(), timeout=self.limit,
                                    max_retries=self.max_retries, http_client=self.http())
