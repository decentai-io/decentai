"""The language-model providers the platform knows by name.

One list, read by both sides: the backend accepts a connection for a
provider on it and serves it to the page that draws the form; the
runtime reads which protocol the provider speaks and builds the
connector for it. Before this each side kept a list of its own, and so
did the page, and a provider was three edits that had to agree.

The list is ``llm_providers.json`` beside this file, written by
``generate_llm_providers.py`` from models.dev. An entry is data:

    id        what a connection stores
    name      what a person reads
    protocol  ``openai`` (chat completions), ``anthropic`` (messages)
              or ``bedrock`` (Amazon's Converse)
    endpoint  where the provider answers. A ``<blank>`` in it is the
              customer's own part — an account, a region — which the
              person fills in; the custom entry's is empty, the whole
              address being theirs to type

``llm_models.json``, written with it, lists the models each provider is
known to serve — an offer for the form, never a gate: a connection may
name any model.

This module holds no key and reaches no network, and it does not decide
where a chat's request goes: a connection carries its own endpoint, which
the person may have changed to a gateway of theirs. The catalog's
address is only what the form starts with.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Tuple


class LlmProviders:
    PATH: ClassVar[Path] = Path(__file__).with_name("llm_providers.json")
    MODELS_PATH: ClassVar[Path] = Path(__file__).with_name("llm_models.json")
    PROTOCOLS: ClassVar[Tuple[str, ...]] = ("openai", "anthropic", "bedrock")

    _entries: ClassVar[Optional[Dict[str, Dict[str, str]]]] = None
    _models: ClassVar[Optional[Dict[str, List[List[str]]]]] = None

    @classmethod
    def _load(cls) -> Dict[str, Dict[str, str]]:
        if cls._entries is None:
            document = json.loads(cls.PATH.read_text(encoding="utf-8"))
            cls._entries = {
                entry["id"]: dict(entry) for entry in document["providers"]
            }
        return cls._entries

    @classmethod
    def all(cls) -> List[Dict[str, str]]:
        """Every provider, in the order a person is shown them."""
        return [dict(entry) for entry in cls._load().values()]

    @classmethod
    def ids(cls) -> Tuple[str, ...]:
        return tuple(cls._load())

    @classmethod
    def models(cls, provider: Any) -> List[Dict[str, str]]:
        """The models a provider is known to serve, as ``{id, name}``;
        empty for a provider the list says nothing of."""
        if cls._models is None:
            cls._models = json.loads(
                cls.MODELS_PATH.read_text(encoding="utf-8"))["models"]
        return [{"id": model_id, "name": name} for model_id, name in
                cls._models.get(str(provider or "").strip().lower(), [])]

    @staticmethod
    def unfilled(endpoint: Any) -> bool:
        """Whether an address still carries a blank from the catalog."""
        return "<" in str(endpoint or "") or ">" in str(endpoint or "")

    @classmethod
    def find(cls, provider: Any) -> Optional[Dict[str, str]]:
        entry = cls._load().get(str(provider or "").strip().lower())
        return dict(entry) if entry else None
