"""Asking a provider whether a key works, and which models it serves.

A connection is saved by a person who has just pasted a key. Whether the
key is right is something only the provider can say, and the place to
hear it is the form — not the first chat, where a wrong key reads as the
assistant being broken. So before a connection is saved the provider is
asked one small thing with that key, and its answer is told to the
person in three words: it works, it was refused, or nobody answered.

The small thing is the provider's own list of models, which costs
nothing and which every protocol but one offers. Bedrock's runtime has
no list a key can read, so it is asked for a reply of a few tokens from
the model the connection starts with.

Azure is asked the same way and for the same reason: its list names
models, and a connection there names a DEPLOYMENT of the customer's own,
which only a request to it can find. A deployment that is not there is a
refusal, said with its name.

The same list is what a server the catalog says nothing about is offered
by: a model on the person's own machine, or a gateway of theirs, names
its models here and nowhere else.

This is not how a chat reaches a model — that is the runtime's
connectors (ai_runtime/llms). Nothing here holds a key past one request.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple
from urllib.parse import quote

import httpx

from contracts.llm_providers import LlmProviders


class ProviderProbe:
    #: The provider said the key works.
    WORKS = "works"
    #: The provider said no: the key is wrong, or may not do this.
    REFUSED = "refused"
    #: Nothing answered at that address.
    UNREACHABLE = "unreachable"
    #: It answered, and not in a way that says either: a server with no
    #: list of models, a model name it does not know. Saved all the same.
    UNKNOWN = "unknown"

    TIMEOUT = 15.0
    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(self, provider: Any, endpoint: Any, api_key: Any, model: Any = ""):
        self.provider = str(provider or "").strip().lower()
        self.api_key = str(api_key or "")
        self.model = str(model or "").strip()
        endpoint = LlmProviders.settled(self.provider, endpoint)
        route = LlmProviders.route(self.provider, self.model, endpoint) or {}
        self.protocol = route.get("protocol", "")
        self.endpoint = str(route.get("endpoint") or "").strip().rstrip("/")
        #: The connection names a deployment: the list says nothing of it.
        self.by_deployment = LlmProviders.by_deployment(self.provider)

    # ------------------------------------------------------------------
    def check(self) -> Dict[str, str]:
        """``{outcome, reason}``: one of the four outcomes above, and the
        sentence to show beside it."""
        outcome, reason, _ = self._ask()
        return {"outcome": outcome, "reason": reason}

    def models(self) -> List[str]:
        """The ids the provider lists, or nothing when it would not say."""
        if self.by_deployment:
            return []   # its list is of models, never of deployments
        outcome, _, listed = self._ask()
        return listed if outcome == self.WORKS else []

    # ------------------------------------------------------------------
    def _ask(self) -> Tuple[str, str, List[str]]:
        if not self.protocol or not self.endpoint:
            return self.UNKNOWN, "There is no address to ask.", []
        try:
            with self._client() as client:
                response = self._request(client)
        except httpx.HTTPError as exc:
            return self.UNREACHABLE, (
                f"Nothing answered at {self.endpoint}: {self._plain(exc)}"), []
        if response.status_code in (401, 403):
            return self.REFUSED, (
                "The provider refused the key: " + self._reason(response)), []
        if self.by_deployment and response.status_code == 404:
            return self.REFUSED, (
                f"Azure has no deployment named '{self.model}' at "
                f"{self.endpoint}. Check the endpoint and the deployment's "
                f"name in the Azure portal. Azure said: {self._reason(response)}"), []
        if response.status_code >= 400:
            return self.UNKNOWN, (
                f"The provider answered {response.status_code}, which does "
                f"not say whether the key works: {self._reason(response)}"), []
        return self.WORKS, "", self._listed(response)

    def _client(self) -> httpx.Client:
        """The seam a test stands a stub in."""
        return httpx.Client(timeout=self.TIMEOUT)

    def _request(self, client: httpx.Client) -> httpx.Response:
        if self.protocol == "anthropic":
            return client.get(f"{self.endpoint}/v1/models", params={"limit": 1000}, headers={
                "x-api-key": self.api_key,
                "anthropic-version": self.ANTHROPIC_VERSION})
        if self.protocol == "gemini":
            return client.get(f"{self.endpoint}/models", params={"pageSize": 1000},
                              headers={"x-goog-api-key": self.api_key})
        if self.protocol == "bedrock":
            return client.post(
                f"{self.endpoint}/model/{quote(self.model, safe='')}/converse",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"messages": [{"role": "user", "content": [{"text": "Hi"}]}],
                      "inferenceConfig": {"maxTokens": 16}})
        if self.by_deployment:
            # A few tokens from the deployment itself: the one question
            # whose answer says the endpoint, the key and the name are
            # all right.
            return client.post(
                f"{self.endpoint}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}",
                         "api-key": self.api_key},
                json={"model": self.model,
                      "messages": [{"role": "user", "content": "Hi"}],
                      "max_completion_tokens": 16})
        # OpenAI's two protocols, and every server that speaks either.
        return client.get(f"{self.endpoint}/models",
                          headers={"Authorization": f"Bearer {self.api_key}"})

    def _listed(self, response: httpx.Response) -> List[str]:
        """The model ids in a list answer, whichever protocol's: under
        ``data`` (OpenAI, Anthropic) or ``models`` (Gemini, whose names
        carry a ``models/`` in front)."""
        try:
            answer = response.json()
        except ValueError:
            return []
        if not isinstance(answer, dict):
            return []
        rows = answer.get("data") or answer.get("models") or []
        found: List[str] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("id") or row.get("name") or "").strip()
            name = name[len("models/"):] if name.startswith("models/") else name
            if name and name not in found:
                found.append(name)
        return sorted(found, key=str.casefold)

    @staticmethod
    def _reason(response: httpx.Response) -> str:
        """Why, in the provider's words, kept short."""
        try:
            answer = response.json()
        except ValueError:
            answer = None
        text = ""
        if isinstance(answer, dict):
            error = answer.get("error")
            text = str(
                (error.get("message") if isinstance(error, dict) else error)
                or answer.get("message") or "")
        return (text or response.text or "no reason given").strip()[:300]

    @staticmethod
    def _plain(exc: Exception) -> str:
        return (str(exc) or type(exc).__name__).strip()[:200]
