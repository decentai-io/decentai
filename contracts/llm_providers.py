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
    protocol  the wire format it speaks, one of ``PROTOCOLS`` below
    endpoint  where the provider answers. A ``<blank>`` in it is the
              customer's own part — an account, a region — which the
              person fills in; the custom entry's is empty, the whole
              address being theirs to type
    popular   set on the few most people look for, so a page can show
              them first and the other two hundred behind a search

``llm_models.json``, written with it, lists the models each provider is
known to serve — an offer for a page, never a gate: any model may be
named. A model there is data too:

    id, name   the provider's own id for it, and a name to read
    kind       ``chat`` (answers in words and calls tools),
               ``embedding`` or ``transcription``
    images, reasoning, efforts, context, output
               what it can do, where models.dev says: read a picture,
               think before answering and how hard it may be asked to,
               and the sizes of its window and of one reply
    protocol, endpoint
               set only where this model is reached differently from
               its provider's others — a gateway that serves Claude
               over Anthropic's protocol at a second address (``route``)

This module holds no key and reaches no network, and it does not decide
where a chat's request goes: a connection carries its own endpoint, which
the person may have changed to a gateway of theirs. The catalog's
address is only what the form starts with.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Tuple


class LlmProviders:
    PATH: ClassVar[Path] = Path(__file__).with_name("llm_providers.json")
    MODELS_PATH: ClassVar[Path] = Path(__file__).with_name("llm_models.json")
    #: The wire formats a provider may name, each spoken by one
    #: connector (ai_runtime/llms/factory.py): OpenAI's chat completions
    #: — which is also what every OpenAI-compatible server speaks — and
    #: its Responses, Anthropic's messages, Google's generateContent and
    #: Amazon's Converse.
    PROTOCOLS: ClassVar[Tuple[str, ...]] = (
        "openai", "openai-responses", "anthropic", "gemini", "bedrock")

    #: ``<aws-region>``: the customer's own part of an address.
    BLANK: ClassVar[re.Pattern] = re.compile(r"<([^<>]+)>")

    _entries: ClassVar[Optional[Dict[str, Dict[str, Any]]]] = None
    _models: ClassVar[Optional[Dict[str, List[Dict[str, Any]]]]] = None

    @classmethod
    def _load(cls) -> Dict[str, Dict[str, Any]]:
        if cls._entries is None:
            document = json.loads(cls.PATH.read_text(encoding="utf-8"))
            cls._entries = {
                entry["id"]: dict(entry) for entry in document["providers"]
            }
        return cls._entries

    @classmethod
    def all(cls) -> List[Dict[str, Any]]:
        """Every provider, in the order a person is shown them."""
        return [dict(entry) for entry in cls._load().values()]

    @classmethod
    def ids(cls) -> Tuple[str, ...]:
        return tuple(cls._load())

    @classmethod
    def models(cls, provider: Any, kind: str = "") -> List[Dict[str, Any]]:
        """The models a provider is known to serve, newest first, and
        only those of one ``kind`` when one is named; empty for a
        provider the list says nothing of."""
        if cls._models is None:
            cls._models = json.loads(
                cls.MODELS_PATH.read_text(encoding="utf-8"))["models"]
        return [dict(model) for model in
                cls._models.get(str(provider or "").strip().lower(), [])
                if not kind or model["kind"] == kind]

    @classmethod
    def model(cls, provider: Any, model_id: Any) -> Optional[Dict[str, Any]]:
        """One model of a provider, or None for one the list does not
        know — which is no refusal: it is asked for by the id given."""
        wanted = str(model_id or "").strip()
        return next((model for model in cls.models(provider)
                     if model["id"] == wanted), None)

    @classmethod
    def route(cls, provider: Any, model_id: Any, endpoint: Any) -> Optional[Dict[str, str]]:
        """The protocol and the address a request for this model goes
        by, or None for a provider that is in no catalog.

        Both are the provider's, except for a model the catalog says is
        reached differently — and then only while the connection still
        points at the provider's own address. One the person pointed at
        a gateway of theirs is theirs: it is asked as the provider is,
        where they said.

        A model's own address may carry the provider's blanks
        (``<azure-resource-name>``); they are read out of the address
        the connection holds, so nobody is asked for them twice."""
        entry = cls.find(provider)
        if entry is None:
            return None
        address = str(endpoint or "").strip() or entry["endpoint"]
        chosen = {"protocol": entry["protocol"], "endpoint": address}
        model = cls.model(provider, model_id)
        if not model or not (model.get("protocol") or model.get("endpoint")):
            return chosen
        filled = cls._blanks(entry["endpoint"], address)
        if filled is None:
            return chosen
        own = cls.BLANK.sub(
            lambda blank: filled.get(blank.group(1), blank.group(0)),
            model.get("endpoint") or address)
        if cls.unfilled(own):
            return chosen
        return {"protocol": model.get("protocol") or entry["protocol"],
                "endpoint": own}

    @classmethod
    def _blanks(cls, template: str, address: str) -> Optional[Dict[str, str]]:
        """What an address has in each blank of the catalog's, or None
        when it is not that address at all."""
        if not template:
            return None
        pattern, position = "", 0
        for number, blank in enumerate(cls.BLANK.finditer(template.rstrip("/"))):
            pattern += re.escape(template[position:blank.start()]) + f"(?P<b{number}>[^/]+?)"
            position = blank.end()
        pattern += re.escape(template.rstrip("/")[position:])
        found = re.fullmatch(pattern, address.rstrip("/"))
        if found is None:
            return None
        names = [blank.group(1) for blank in cls.BLANK.finditer(template)]
        return {name: found.group(f"b{number}") for number, name in enumerate(names)}

    @staticmethod
    def unfilled(endpoint: Any) -> bool:
        """Whether an address still carries a blank from the catalog."""
        return "<" in str(endpoint or "") or ">" in str(endpoint or "")

    @classmethod
    def find(cls, provider: Any) -> Optional[Dict[str, Any]]:
        entry = cls._load().get(str(provider or "").strip().lower())
        return dict(entry) if entry else None
