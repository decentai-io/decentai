"""Write contracts/llm_providers.json from a checkout of models.dev.

models.dev (https://github.com/anomalyco/models.dev) is the open
database of language-model providers: one folder per provider, whose
``provider.toml`` names the provider, the client library that speaks to
it, and the address it answers at. This reads those files and keeps the
providers the platform's two connectors can reach as they are — the
ones that speak OpenAI's chat-completions protocol or Anthropic's
messages protocol at an address that is the same for every customer.

    git clone https://github.com/anomalyco/models.dev
    python contracts/generate_llm_providers.py path/to/models.dev

The catalog is a file in this repository, not a request made while the
platform runs: an install with no way out to the internet lists the
same providers as any other, and what a deployment offers is what was
reviewed in a commit. Refreshing it is running this again and reading
the diff.

What it deliberately leaves out:

- A provider with a protocol of its own, or a way of signing in that is
  not a key — Amazon Bedrock, Google Vertex, Azure, GitHub Copilot. Each
  would be a connector, and a connector is code somebody reviews.
- A provider whose address differs per customer (it carries a
  ``${...}`` in models.dev), and one that is on the person's own machine.
  Both are reached with ``openai_compatible`` and the address typed.
- Models. A connection names its model in the provider's own words, and
  a list kept here would be out of date the week it was written.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit


class ProviderCatalogWriter:
    OUTPUT = Path(__file__).with_name("llm_providers.json")
    REPOSITORY = "https://github.com/anomalyco/models.dev"

    #: The client libraries whose protocol a connector here speaks, when
    #: the provider's file gives an address to speak it at.
    PROTOCOLS = {
        "@ai-sdk/openai-compatible": "openai",
        "@ai-sdk/anthropic": "anthropic",
    }

    #: Providers models.dev reaches through a library of their own, which
    #: therefore carry no address there, and which also answer OpenAI's
    #: protocol (Anthropic: its own) at a documented address. The id on
    #: the left is models.dev's; two more values give the id and the
    #: name this platform already used, so that connections made before
    #: the catalog keep the provider they were saved under.
    DOCUMENTED = {
        "openai": ("openai", "https://api.openai.com/v1"),
        "anthropic": ("anthropic", "https://api.anthropic.com"),
        "google": ("openai", "https://generativelanguage.googleapis.com/v1beta/openai/",
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
    }

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

    def __init__(self, checkout: Path):
        self.checkout = checkout

    def write(self) -> int:
        providers = self.providers()
        document = {
            "schema_version": "1.0",
            "source": {"repository": self.REPOSITORY, "commit": self._commit()},
            "providers": providers,
        }
        self.OUTPUT.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        return len(providers)

    def providers(self) -> List[Dict[str, str]]:
        kept: Dict[str, Dict[str, str]] = {}
        for path in sorted((self.checkout / "providers").glob("*/provider.toml")):
            entry = self._entry(path.parent.name, tomllib.loads(
                path.read_text(encoding="utf-8")))
            if entry is not None:
                kept[entry["id"]] = entry
        ordered = sorted(kept.values(), key=lambda entry: entry["name"].casefold())
        return ordered + [dict(self.CUSTOM)]

    def _entry(self, provider_id: str, declared: Dict[str, Any]) -> Optional[Dict[str, str]]:
        if provider_id in self.SIGNED_IN:
            return None
        name = str(declared.get("name") or provider_id)
        documented = self.DOCUMENTED.get(provider_id)
        if documented is not None:
            protocol, endpoint, *renamed = documented
            return {
                "id": renamed[0] if renamed else provider_id,
                "name": renamed[1] if len(renamed) > 1 else name,
                "protocol": protocol,
                "endpoint": endpoint,
            }
        protocol = self.PROTOCOLS.get(str(declared.get("npm") or ""))
        endpoint = str(declared.get("api") or "").strip()
        if protocol is None or not self._same_for_everyone(endpoint):
            return None
        return {
            "id": provider_id,
            "name": name,
            "protocol": protocol,
            "endpoint": self._for_connector(protocol, endpoint),
        }

    @staticmethod
    def _same_for_everyone(endpoint: str) -> bool:
        """A public address with nothing left to fill in."""
        if not endpoint or "${" in endpoint:
            return False
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

    def _commit(self) -> str:
        return subprocess.run(
            ["git", "-C", str(self.checkout), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True).stdout.strip()


if __name__ == "__main__":
    if len(sys.argv) != 2 or not (Path(sys.argv[1]) / "providers").is_dir():
        sys.exit("usage: python contracts/generate_llm_providers.py path/to/models.dev")
    count = ProviderCatalogWriter(Path(sys.argv[1])).write()
    print(f"wrote {ProviderCatalogWriter.OUTPUT} ({count} providers)")
