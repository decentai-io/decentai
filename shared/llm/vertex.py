"""Google Vertex AI: a connection's service account key, its project and region, and the short-lived token it signs
in with. The runtime calls models with it; the API server checks a connection with it before it is saved. Google's
library is imported only when an account is used."""

from __future__ import annotations

import json
import re
import threading
from typing import Any
from urllib.parse import urlsplit

#: What the token may do: call Google Cloud's APIs, as the account's roles allow.
SCOPE = "https://www.googleapis.com/auth/cloud-platform"
#: Where a service account's assertion is exchanged for a token: Google's.
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"

_HOST = re.compile(r"^(?P<location>[a-z0-9-]+)-aiplatform\.googleapis\.com$")


class VertexKeyError(ValueError):
    """The key is not a service account's JSON key. Written for the
    person who pasted it."""


class VertexAccount:
    def __init__(self, key: Any, endpoint: Any):
        self.info = self.parse(key)
        self.project = str(self.info["project_id"])
        self.location = self.location_of(endpoint)
        self.host = ("aiplatform.googleapis.com" if self.location == "global"
                     else f"{self.location}-aiplatform.googleapis.com")
        self._credentials = None
        self._lock = threading.Lock()

    @staticmethod
    def parse(key: Any) -> dict[str, Any]:
        """The service account's key, read and checked: never sent
        anywhere until it is one."""
        try:
            info = json.loads(str(key or ""))
        except ValueError:
            raise VertexKeyError(
                "Paste the service account's whole JSON key — the file "
                "Google Cloud gives when a key is added to the account.") from None
        if not isinstance(info, dict) or info.get("type") != "service_account":
            raise VertexKeyError(
                "That JSON is not a service account key "
                "(its \"type\" is not \"service_account\").")
        missing = [name for name in ("project_id", "client_email", "private_key")
                   if not str(info.get(name) or "").strip()]
        if missing:
            raise VertexKeyError(
                "The service account key is missing " + ", ".join(missing) + ".")
        # Google's library posts the signed assertion wherever the key says: only Google's address, or a pasted
        # key could send a signed request anywhere this node reaches.
        token_uri = str(info.get("token_uri") or GOOGLE_TOKEN_URI)
        if token_uri != GOOGLE_TOKEN_URI:
            raise VertexKeyError(
                f"The service account key's token_uri must be {GOOGLE_TOKEN_URI}.")
        return {**info, "token_uri": GOOGLE_TOKEN_URI}

    @staticmethod
    def location_of(endpoint: Any) -> str:
        host = (urlsplit(str(endpoint or "").strip()).hostname or "").lower()
        if host == "aiplatform.googleapis.com":
            return "global"
        found = _HOST.match(host)
        if not found:
            raise VertexKeyError(
                "A Vertex AI address is https://<region>-aiplatform.googleapis.com "
                "— us-central1, europe-west4, global, …")
        return found.group("location")

    @property
    def base(self) -> str:
        """Where this project's models answer, in this region."""
        return (f"https://{self.host}/v1/projects/{self.project}"
                f"/locations/{self.location}")

    def credentials(self):
        """The Google credentials for the key, made once: the Anthropic
        client takes these and keeps its own token fresh."""
        if self._credentials is None:
            from google.oauth2 import service_account

            self._credentials = service_account.Credentials.from_service_account_info(
                self.info, scopes=[SCOPE])
        return self._credentials

    def token(self) -> str:
        """A token for the next call, made again once google-auth says the one held is near its end. Blocking:
        the runtime asks for it on a thread."""
        from google.auth.transport.requests import Request

        with self._lock:
            credentials = self.credentials()
            if not credentials.valid:
                credentials.refresh(Request())
            return str(credentials.token)
