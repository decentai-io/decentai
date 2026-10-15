"""Google Vertex AI: a connection's service account, its project and its
region, and the short-lived token it signs in with.

A Vertex connection's key is the JSON key of a Google Cloud service
account, pasted whole. The project is the one the key names; the region
is the one blank in the catalog's address
(``https://<google-vertex-location>-aiplatform.googleapis.com``), with
``global`` reached at ``aiplatform.googleapis.com``. Calls are signed
with an OAuth token made from the key, good for an hour and made again
before it runs out.

Shared by the runtime, which calls the model (ai_runtime/llms/connector/
vertex.py), and the backend, which checks a connection before it is
saved (backend/api/services/llm_probe.py). The Google library is
imported when an account is used, not before.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Dict
from urllib.parse import urlsplit

#: What the token may do: call Google Cloud's APIs, as the account's
#: roles allow (Vertex AI User is what a model call needs).
SCOPE = "https://www.googleapis.com/auth/cloud-platform"

_HOST = re.compile(r"^(?P<location>[a-z0-9-]+)-aiplatform\.googleapis\.com$")


class VertexKeyError(ValueError):
    """The key is not a service account's JSON key. Written for the
    person who pasted it."""


class VertexAccount:
    #: Seconds before a token's end at which it is made again.
    EARLY = 300

    def __init__(self, key: Any, endpoint: Any):
        self.info = self.parse(key)
        self.project = str(self.info["project_id"])
        self.location = self.location_of(endpoint)
        self.host = ("aiplatform.googleapis.com" if self.location == "global"
                     else f"{self.location}-aiplatform.googleapis.com")
        self._credentials = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    @staticmethod
    def parse(key: Any) -> Dict[str, Any]:
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
        return info

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

    # ------------------------------------------------------------------
    def credentials(self):
        """The Google credentials for the key, made once: the Anthropic
        client takes these and keeps its own token fresh."""
        if self._credentials is None:
            from google.oauth2 import service_account

            self._credentials = service_account.Credentials.from_service_account_info(
                self.info, scopes=[SCOPE])
        return self._credentials

    def token(self) -> str:
        """A token for the next call: the one held, while it has more
        than a few minutes left, else a new one. Blocking — the runtime
        asks for it on a thread."""
        import datetime

        from google.auth.transport.requests import Request

        with self._lock:
            credentials = self.credentials()
            expiry = credentials.expiry
            fresh = (credentials.token and expiry is not None
                     and expiry - datetime.timedelta(seconds=self.EARLY)
                     > datetime.datetime.utcnow())
            if not fresh:
                credentials.refresh(Request())
            return str(credentials.token)
