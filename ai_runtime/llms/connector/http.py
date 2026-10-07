"""What the connectors that speak plain HTTP have in common.

OpenAI's and Anthropic's protocols come with a client library that
times a call out, retries a busy server and words a refusal. Bedrock's
Converse and Gemini's generateContent are reached here with no library
of theirs — two shapes to translate and one POST each — so the same
three things are done once, in this class, over the HTTP client the
runtime already has.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

import httpx

from ai_runtime.llms.connector.tools import setting


class ProviderError(RuntimeError):
    """A provider refused or failed. The message is its own, so that the
    cycle's reading of a refusal (too long, no pictures) works on it as
    on any provider's; ``status_code`` is the status it answered with,
    under the name the client libraries give theirs."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class HttpConnector:
    #: The provider's name, as a refusal names it.
    NAME = "The provider"

    #: The answers worth asking again for: the server was busy or
    #: broken, not the request wrong.
    RETRIED = (408, 409, 429, 500, 502, 503, 504, 529)
    #: Seconds before the first retry; doubled for each one after.
    RETRY_WAIT = 0.5

    def __init__(self, config: dict):
        self.api_key = str(config.get("api_key") or "")
        self.model = str(config.get("model") or "")
        self.endpoint = str(config.get("endpoint") or "").strip().rstrip("/")
        for name in ("api_key", "model", "endpoint"):
            if not getattr(self, name):
                raise ValueError(f"{self.NAME} connector requires {name}")
        # A hung provider call must not stall a reasoning turn forever;
        # a busy one is asked again, as the client libraries do.
        self.timeout = setting(config, "timeout_seconds", 60, 1, 600)
        self.max_retries = int(setting(config, "max_retries", 2, 0, 10))

    def _client(self) -> httpx.AsyncClient:
        """The plain client, so that the way this machine reaches the
        internet (HTTPS_PROXY, a CA bundle) applies as it does to the
        other connectors."""
        return httpx.AsyncClient(timeout=self.timeout)

    @staticmethod
    def _reason(response: httpx.Response) -> str:
        """Why it refused, in the provider's words: ``message`` at the
        top (Bedrock) or under ``error`` (Gemini), else the body."""
        try:
            answer = response.json()
        except ValueError:
            return response.text
        if isinstance(answer, dict):
            error = answer.get("error")
            nested = error.get("message") if isinstance(error, dict) else None
            return str(answer.get("message") or nested or response.text)
        return response.text

    async def _post(self, url: str, body: dict, headers: Dict[str, str]) -> Any:
        attempt = 0
        async with self._client() as client:
            while True:
                try:
                    response = await client.post(url, json=body, headers={
                        "Accept": "application/json", **headers})
                except httpx.TransportError:
                    if attempt >= self.max_retries:
                        raise
                else:
                    if response.status_code < 400:
                        return response.json()
                    if response.status_code not in self.RETRIED \
                            or attempt >= self.max_retries:
                        raise ProviderError(
                            f"{self.NAME} answered {response.status_code}: "
                            f"{self._reason(response)[:600]}",
                            response.status_code)
                await asyncio.sleep(self.RETRY_WAIT * (2 ** attempt))
                attempt += 1
