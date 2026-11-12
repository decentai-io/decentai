"""A provider reached with plain HTTPS, with no client library of its own."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, AsyncIterator
from urllib.parse import quote

import httpx

from .connector import Connector


class HttpConnector(Connector):
    """A signed POST to ``URL``; a provider busy or out of reach asked again after a wait."""

    REQUIRED = ("api_key", "model", "endpoint")
    #: The answers worth asking again for: the server was busy or broken, not the request wrong.
    RETRIED = (408, 409, 429, 500, 502, 503, 504, 529)
    #: Seconds before the first retry; doubled for each one after.
    RETRY_WAIT = 0.5
    #: Where a request goes, from ``endpoint``, ``model`` and ``verb``.
    URL = ""

    def _client(self) -> httpx.AsyncClient:
        return self.http()

    async def close(self) -> None:
        await self.client.aclose()

    async def _auth(self) -> dict[str, str]:
        """The headers that sign one POST."""
        raise NotImplementedError

    def _url(self, verb: str) -> str:
        # The id may be an ARN, whose slashes and colons are part of one path segment.
        return self.URL.format(endpoint=self.endpoint, model=quote(self.model, safe=""), verb=verb)

    @staticmethod
    def _reason(response: httpx.Response) -> str:
        """Why it refused, in the provider's words: ``message`` at the top or under ``error``, else the body."""
        try:
            answer = response.json()
        except ValueError:
            return response.text
        if isinstance(answer, dict):
            error = answer.get("error")
            nested = error.get("message") if isinstance(error, dict) else None
            return str(answer.get("message") or nested or response.text)
        return response.text

    @contextlib.asynccontextmanager
    async def opened(self, verb: str, body: dict[str, Any]) -> AsyncIterator[httpx.Response]:
        """The answer, open for reading. A busy server is asked again here and at no later moment."""
        headers = await self._auth()
        attempt = 0
        while True:
            try:
                response = await self.client.send(
                    self.client.build_request("POST", self._url(verb), json=body, headers=headers), stream=True)
            except httpx.TransportError:
                if attempt >= self.RETRIES:
                    raise
            else:
                if response.status_code < 300:
                    break
                await response.aread()
                await response.aclose()
                if response.status_code not in self.RETRIED or attempt >= self.RETRIES:
                    raise self.Failed(f"{self.NAME} answered {response.status_code}: {self._reason(response)[:600]}",
                                      response.status_code)
            await asyncio.sleep(self.RETRY_WAIT * (2 ** attempt))
            attempt += 1
        try:
            yield response
        finally:
            await response.aclose()
