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

An address that differs per customer carries a ``${NAME}`` in
models.dev; here it becomes ``<name>``, a blank the person fills in on
the form. The angle brackets cannot be part of an address, so a
connection saved with one still in it is refused rather than tried.

What it deliberately leaves out:

- A provider reached only by signing in, or by a credential that is not
  one string a person can paste — GitHub Copilot, Google Vertex. Amazon
  Bedrock is here because it issues an API key; its protocol is its own,
  so it names the connector written for it.
- A model on the person's own machine. It is reached with
  ``openai_compatible`` and the address typed, since where that machine
  is, as seen from the platform, is nothing a catalog can know.

Beside the providers it writes ``llm_models.json``: for each provider
kept, the models models.dev lists for it, as the provider's own id and a
name to read. The form offers them so that nobody has to find and copy
an id like ``us.anthropic.claude-sonnet-4-5-20250929-v1:0``. The list is
an offer and never a gate: a model newer than this file is typed, and
the store accepts any model name.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit


class ProviderCatalogWriter:
    OUTPUT = Path(__file__).with_name("llm_providers.json")
    MODELS_OUTPUT = Path(__file__).with_name("llm_models.json")
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
        "azure": ("openai", "https://${AZURE_RESOURCE_NAME}.openai.azure.com/openai/v1"),
        "amazon-bedrock": ("bedrock", "https://bedrock-runtime.${AWS_REGION}.amazonaws.com"),
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
        self.MODELS_OUTPUT.write_text(
            json.dumps({"schema_version": "1.0", "source": document["source"],
                        "models": self.models()},
                       indent=0, ensure_ascii=False) + "\n",
            encoding="utf-8")
        return len(providers)

    def models(self) -> Dict[str, List[List[str]]]:
        """For each provider kept: ``[id, name]`` for every model
        models.dev lists under it, by name. The id is the file's path
        under the provider's ``models`` folder, which is how models.dev
        spells an id with a slash in it."""
        listed: Dict[str, List[List[str]]] = {}
        for path in sorted((self.checkout / "providers").glob("*/provider.toml")):
            entry = self._entry(path.parent.name, tomllib.loads(
                path.read_text(encoding="utf-8")))
            folder = path.parent / "models"
            if entry is None or not folder.is_dir():
                continue
            found = []
            for file in sorted(folder.rglob("*.toml")):
                if not file.is_file():  # a link whose target is gone
                    continue
                declared = tomllib.loads(file.read_text(encoding="utf-8"))
                if declared.get("status") == "deprecated":
                    continue
                model_id = file.relative_to(folder).with_suffix("").as_posix()
                found.append([model_id, self._model_name(declared) or model_id])
            if found:
                listed[entry["id"]] = sorted(
                    found, key=lambda model: (model[1].casefold(), model[0]))
        return listed

    def _model_name(self, declared: Dict[str, Any]) -> str:
        """The model's name: the provider's own file says it, or the
        file of the model it serves does."""
        if declared.get("name"):
            return str(declared["name"])
        base = self.checkout / "models" / f"{declared.get('base_model') or ''}.toml"
        if declared.get("base_model") and base.is_file():
            return str(tomllib.loads(base.read_text(encoding="utf-8")).get("name") or "")
        return ""

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
                "endpoint": self._with_blanks(endpoint),
            }
        protocol = self.PROTOCOLS.get(str(declared.get("npm") or ""))
        endpoint = str(declared.get("api") or "").strip()
        if protocol is None or not self._public(endpoint):
            return None
        return {
            "id": provider_id,
            "name": name,
            "protocol": protocol,
            "endpoint": self._with_blanks(self._for_connector(protocol, endpoint)),
        }

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

    def _commit(self) -> str:
        return subprocess.run(
            ["git", "-C", str(self.checkout), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True).stdout.strip()


if __name__ == "__main__":
    if len(sys.argv) != 2 or not (Path(sys.argv[1]) / "providers").is_dir():
        sys.exit("usage: python contracts/generate_llm_providers.py path/to/models.dev")
    count = ProviderCatalogWriter(Path(sys.argv[1])).write()
    print(f"wrote {ProviderCatalogWriter.OUTPUT} ({count} providers)")
