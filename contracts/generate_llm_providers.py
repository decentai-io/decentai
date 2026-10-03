"""Write contracts/llm_providers.json and llm_models.json from models.dev.

models.dev (https://github.com/anomalyco/models.dev) is the open
database of language-model providers. It publishes all of it as one
file, ``https://models.dev/api.json``: for each provider its name, the
client library that speaks to it and the address it answers at, and for
each of its models what the model can do. This reads that file and keeps
what the platform's connectors can reach with a key.

    python contracts/generate_llm_providers.py                 # the published file
    python contracts/generate_llm_providers.py path/to/api.json

The published file is read, not a checkout of the repository: models.dev
names a model's file after its id, and an id like ``amazon.nova-pro-v1:0``
is no file name on Windows.

The catalog is a file in this repository, not a request made while the
platform runs: an install with no way out to the internet lists the
same providers as any other, and what a deployment offers is what was
reviewed in a commit. Refreshing it is running this again and reading
the diff, which is done for each release.

An address that differs per customer carries a ``${NAME}`` in
models.dev; here it becomes ``<name>``, a blank the person fills in.
The angle brackets cannot be part of an address, so a connection saved
with one still in it is refused rather than tried.

What it deliberately leaves out:

- A provider reached only by signing in, or by a credential that is not
  one string a person can paste — GitHub Copilot, Google Vertex. Amazon
  Bedrock is here because it issues an API key.
- A model on the person's own machine. It is reached with
  ``openai_compatible`` and the address typed, since where that machine
  is, as seen from the platform, is nothing a catalog can know.
- A model that neither embeds nor both answers in words and calls
  tools — an image maker, a model with no tool calling. The runtime
  could not think with it. Left out of the offer only: any model may
  still be named by its id.
- A model its provider has marked deprecated.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit


class ProviderCatalogWriter:
    OUTPUT = Path(__file__).with_name("llm_providers.json")
    MODELS_OUTPUT = Path(__file__).with_name("llm_models.json")
    SOURCE = "https://models.dev/api.json"

    #: The client libraries whose protocol a connector speaks, when the
    #: provider gives an address to speak it at.
    PROTOCOLS = {
        "@ai-sdk/openai-compatible": "openai",
        "@openrouter/ai-sdk-provider": "openai",
        "@ai-sdk/openai": "openai-responses",
        "@ai-sdk/anthropic": "anthropic",
        "@ai-sdk/google": "gemini",
        "@ai-sdk/amazon-bedrock": "bedrock",
    }

    #: Which of OpenAI's two protocols, where models.dev says it apart
    #: from the library: its ``shape``.
    SHAPES = {"responses": "openai-responses", "completions": "openai"}

    #: Providers models.dev reaches through a library of their own, which
    #: therefore carry no address there, with the protocol they answer
    #: and the documented address they answer it at. The id on the left
    #: is models.dev's; two more values give the id and the name this
    #: platform uses for it.
    DOCUMENTED = {
        "openai": ("openai-responses", "https://api.openai.com/v1"),
        "anthropic": ("anthropic", "https://api.anthropic.com"),
        "google": ("gemini", "https://generativelanguage.googleapis.com/v1beta",
                   "gemini", "Google Gemini"),
        "openrouter": ("openai", "https://openrouter.ai/api/v1"),
        "groq": ("openai", "https://api.groq.com/openai/v1"),
        "mistral": ("openai", "https://api.mistral.ai/v1"),
        "xai": ("openai", "https://api.x.ai/v1"),
        "cerebras": ("openai", "https://api.cerebras.ai/v1"),
        "cohere": ("openai", "https://api.cohere.ai/compatibility/v1"),
        "deepinfra": ("openai", "https://api.deepinfra.com/v1/openai"),
        "perplexity": ("openai", "https://api.perplexity.ai"),
        "togetherai": ("openai", "https://api.together.xyz/v1"),
        "azure": ("openai", "https://${AZURE_RESOURCE_NAME}.openai.azure.com/openai/v1"),
        "amazon-bedrock": ("bedrock", "https://bedrock-runtime.${AWS_REGION}.amazonaws.com"),
    }

    #: The few most people look for, by the id the catalog gives them.
    #: A page shows these first and the rest behind a search.
    POPULAR = frozenset({
        "openai", "anthropic", "gemini", "amazon-bedrock", "azure",
        "openrouter", "groq", "mistral", "xai", "deepseek",
    })

    #: Speech-to-text models, which models.dev does not list: the ones
    #: served on OpenAI's audio protocol, by the catalog's provider id.
    TRANSCRIPTION = {
        "openai": (("gpt-4o-transcribe", "GPT-4o Transcribe"),
                   ("gpt-4o-mini-transcribe", "GPT-4o mini Transcribe"),
                   ("whisper-1", "Whisper")),
        "groq": (("whisper-large-v3-turbo", "Whisper Large v3 Turbo"),
                 ("whisper-large-v3", "Whisper Large v3")),
    }

    #: ``${ACCOUNT_ID}``, as models.dev marks what differs per customer.
    BLANK = re.compile(r"\$\{([A-Za-z0-9_]+)\}")

    #: Listed in models.dev with an address, and not reachable with a
    #: key: the person signs in, and the token is exchanged for another.
    SIGNED_IN = frozenset({"github-copilot"})

    #: The one entry that is not a provider: any server that speaks
    #: OpenAI's protocol, at an address the person types.
    CUSTOM = {
        "id": "openai_compatible",
        "name": "Custom / OpenAI-compatible",
        "protocol": "openai",
        "endpoint": "",
    }

    def __init__(self, declared: Dict[str, Any], source: Optional[Dict[str, str]] = None):
        #: models.dev's file: provider id → what it declares.
        self.declared = declared
        self.source = source or {"url": self.SOURCE}

    @classmethod
    def read(cls, where: str = "") -> "ProviderCatalogWriter":
        """From the published file, or from a copy of it on disk."""
        if where and Path(where).is_file():
            raw = Path(where).read_bytes()
        else:
            request = urllib.request.Request(
                where or cls.SOURCE, headers={"User-Agent": "decentai-catalog"})
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
        return cls(json.loads(raw.decode("utf-8")), {
            "url": cls.SOURCE, "sha256": hashlib.sha256(raw).hexdigest()})

    def write(self) -> int:
        providers = self.providers()
        self.OUTPUT.write_text(
            json.dumps({"schema_version": "2.0", "source": self.source,
                        "providers": providers},
                       indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        self.MODELS_OUTPUT.write_text(self._models_text(), encoding="utf-8")
        return len(providers)

    def _models_text(self) -> str:
        """The models file, one model to a line: a refresh then reads as
        the models that came and went, not as a file rewritten."""
        listed = self.models()
        blocks = []
        for provider_id in sorted(listed):
            rows = ",\n".join(json.dumps(model, ensure_ascii=False)
                              for model in listed[provider_id])
            blocks.append(f"{json.dumps(provider_id)}: [\n{rows}\n]")
        return ('{"schema_version": "2.0", "source": '
                + json.dumps(self.source) + ', "models": {\n'
                + ",\n".join(blocks) + "\n}}\n")

    # ------------------------------------------------------------------
    def providers(self) -> List[Dict[str, Any]]:
        kept: Dict[str, Dict[str, Any]] = {}
        for provider_id in sorted(self.declared):
            entry = self._entry(provider_id, self.declared[provider_id])
            if entry is not None:
                kept[entry["id"]] = entry
        ordered = sorted(kept.values(), key=lambda entry: entry["name"].casefold())
        return ordered + [dict(self.CUSTOM)]

    def _entry(self, provider_id: str, declared: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if provider_id in self.SIGNED_IN:
            return None
        name = str(declared.get("name") or provider_id)
        documented = self.DOCUMENTED.get(provider_id)
        if documented is not None:
            protocol, endpoint, *renamed = documented
            entry = {
                "id": renamed[0] if renamed else provider_id,
                "name": renamed[1] if len(renamed) > 1 else name,
                "protocol": protocol,
                "endpoint": self._with_blanks(endpoint),
            }
        else:
            protocol = self._protocol(declared.get("npm"), declared.get("shape"))
            endpoint = str(declared.get("api") or "").strip()
            if protocol is None or not self._public(endpoint):
                return None
            entry = {
                "id": provider_id,
                "name": name,
                "protocol": protocol,
                "endpoint": self._with_blanks(self._for_connector(protocol, endpoint)),
            }
        if entry["id"] in self.POPULAR:
            entry["popular"] = True
        return entry

    def _protocol(self, library: Any, shape: Any = None) -> Optional[str]:
        """The protocol a client library speaks, or None for one no
        connector does. ``shape`` chooses between OpenAI's two."""
        protocol = self.PROTOCOLS.get(str(library or ""))
        if protocol in self.SHAPES.values() and shape in self.SHAPES:
            return self.SHAPES[shape]
        return protocol

    # ------------------------------------------------------------------
    def models(self) -> Dict[str, List[Dict[str, Any]]]:
        """For each provider kept, the models it is known to serve that
        the platform has a use for, newest first."""
        listed: Dict[str, List[Dict[str, Any]]] = {}
        for provider_id in sorted(self.declared):
            declared = self.declared[provider_id]
            entry = self._entry(provider_id, declared)
            if entry is None:
                continue
            found = [(str(model.get("release_date") or ""), row)
                     for model in (declared.get("models") or {}).values()
                     for row in [self._model(entry, declared, model)]
                     if row is not None]
            found.sort(key=lambda dated: dated[1]["name"].casefold())
            found.sort(key=lambda dated: dated[0], reverse=True)
            rows = [row for _, row in found] + [
                {"id": model_id, "name": name, "kind": "transcription"}
                for model_id, name in self.TRANSCRIPTION.get(entry["id"], ())]
            if rows:
                listed[entry["id"]] = rows
        return listed

    def _model(self, entry: Dict[str, Any], provider: Dict[str, Any],
               declared: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if declared.get("status") == "deprecated":
            return None
        kind = self._kind(declared)
        if kind is None:
            return None
        model_id = str(declared.get("id") or "")
        row: Dict[str, Any] = {
            "id": model_id, "name": str(declared.get("name") or model_id),
            "kind": kind}
        if kind == "chat":
            if "image" in ((declared.get("modalities") or {}).get("input") or []):
                row["images"] = True
            if declared.get("reasoning"):
                row["reasoning"] = True
            efforts = next(
                (option.get("values") for option in declared.get("reasoning_options") or []
                 if isinstance(option, dict) and option.get("type") == "effort"), None)
            if efforts:
                row["efforts"] = list(efforts)
        for size in ("context", "output"):
            if int((declared.get("limit") or {}).get(size) or 0) > 0:
                row[size] = int(declared["limit"][size])
        if isinstance(declared.get("provider"), dict) and declared["provider"]:
            reached = self._reached(entry, provider, declared["provider"])
            if reached is None:
                return None
            row.update(reached)
        return row

    @staticmethod
    def _kind(declared: Dict[str, Any]) -> Optional[str]:
        """What a model is for, or None for one the platform has no use
        for. models.dev has no word for an embedding model; its id or
        its family says so."""
        named = f"{declared.get('id') or ''} {declared.get('family') or ''}".lower()
        if "embed" in named:
            return "embedding"
        answers = (declared.get("modalities") or {}).get("output") or []
        return "chat" if declared.get("tool_call") and "text" in answers else None

    def _reached(self, entry: Dict[str, Any], provider: Dict[str, Any],
                 own: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """Where one model is reached differently from its provider's
        others: the protocol and the address that differ, nothing when
        neither does, None when no connector speaks its protocol."""
        if own.get("npm"):
            protocol = self._protocol(own["npm"], own.get("shape"))
        else:
            protocol = self.SHAPES.get(own.get("shape")) \
                if entry["protocol"] in self.SHAPES.values() else None
            protocol = protocol or entry["protocol"]
        if protocol is None:
            return None
        # The provider's address as models.dev wrote it, where it did:
        # an Anthropic model at an OpenAI address loses the version
        # path there, not from the catalog's already-fitted one.
        address = str(own.get("api") or provider.get("api") or "").strip()
        if address:
            if not self._public(address):
                return None
            endpoint = self._with_blanks(self._for_connector(protocol, address))
        else:
            endpoint = entry["endpoint"]
        reached = {}
        if protocol != entry["protocol"]:
            reached["protocol"] = protocol
        if endpoint != entry["endpoint"]:
            reached["endpoint"] = endpoint
        return reached

    # ------------------------------------------------------------------
    @classmethod
    def _with_blanks(cls, endpoint: str) -> str:
        """``${ACCOUNT_ID}`` as ``<account-id>``: what the person reads
        on the form, and what the store knows was not filled in."""
        return cls.BLANK.sub(
            lambda found: "<" + found.group(1).lower().replace("_", "-") + ">",
            endpoint)

    @staticmethod
    def _public(endpoint: str) -> bool:
        """An https address that is not this machine's. One that begins
        with a blank has no scheme to check and is left out."""
        parts = urlsplit(endpoint)
        host = (parts.hostname or "").lower()
        return parts.scheme == "https" and host not in ("", "localhost") \
            and not host.startswith("127.")

    @staticmethod
    def _for_connector(protocol: str, endpoint: str) -> str:
        """models.dev writes an Anthropic address with its ``/v1``; the
        Anthropic client this platform uses adds that itself."""
        if protocol == "anthropic" and endpoint.rstrip("/").endswith("/v1"):
            return endpoint.rstrip("/")[: -len("/v1")]
        return endpoint


if __name__ == "__main__":
    if len(sys.argv) > 2:
        sys.exit("usage: python contracts/generate_llm_providers.py [path/to/api.json]")
    writer = ProviderCatalogWriter.read(sys.argv[1] if len(sys.argv) == 2 else "")
    count = writer.write()
    print(f"wrote {ProviderCatalogWriter.OUTPUT} ({count} providers)")
    print(f"wrote {ProviderCatalogWriter.MODELS_OUTPUT} "
          f"({sum(len(rows) for rows in writer.models().values())} models)")
