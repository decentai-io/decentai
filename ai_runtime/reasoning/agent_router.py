"""Which agents a turn is shown, when there are more than fit — by
meaning, not by words.

The frame lists agents one line each and the model opens the one the
request needs. That works for a few dozen. Past the organization's
threshold, this router embeds each agent once — its name and
description, its example prompts, its function descriptions — and the
person's message each turn, keeps the closest candidates by cosine,
has the chat's model rerank them, and the frame lists that shortlist
plus whatever is already open. A request in Arabic lands on an agent
described in English because a multilingual embedding model puts them
in one space; nothing here matches names.

Vectors are computed once per package and embedding model and kept on
disk beside the packages, so a restart re-reads them and a thousand
agents cost one burst of batched calls, once. Everything here is a
courtesy to choosing, never authority: an agent left off the list is
still openable by id, and when the embedding model is missing or
refusing, the frame lists every agent. Nothing here has a timeout of
its own: a slow embedding model is a slow turn, for as long as its
connector waits.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ai_runtime.llms.factory import LLMConnectorFactory
from ai_runtime.prompts import Prompts
from ai_runtime.runtime_logging import RuntimeLoggerFactory


class AgentRouter:
    #: texts per embedding call
    BATCH = 96
    #: how many example prompts and function lines an agent is embedded by
    EXAMPLES_MAX = 12
    FUNCTIONS_MAX = 40
    #: how many a find_agents answers with
    FIND_LIMIT = 20
    #: below this similarity a find is a miss
    FIND_FLOOR = 0.1

    def __init__(self, cache_dir: str | Path, factory=None):
        self.cache_dir = Path(cache_dir)
        self._factory = factory or LLMConnectorFactory.create
        #: connection key -> the embedding connector
        self._connectors: Dict[str, Any] = {}
        #: (model key, digest) -> the agent's vectors
        self._vectors: Dict[Tuple[str, str], List[List[float]]] = {}
        #: one indexing at a time per model, so two chats opening at
        #: once do not embed the same thousand agents twice
        self._locks: Dict[str, asyncio.Lock] = {}
        self.logger = RuntimeLoggerFactory.get_logger(self.__class__.__name__)

    # -- the model ----------------------------------------------------------
    @staticmethod
    def model_key(embedding: Dict[str, Any]) -> str:
        """What vectors are keyed by: the provider, the model and the
        endpoint. A different model is a different space."""
        text = "|".join(str(embedding.get(k) or "") for k in ("provider", "model", "endpoint"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def connector(self, embedding: Dict[str, Any]):
        """The embedding connector, built once per connection and key."""
        key = self.model_key(embedding) + hashlib.sha256(
            str(embedding.get("api_key") or "").encode("utf-8")).hexdigest()[:8]
        connector = self._connectors.get(key)
        if connector is None:
            connector = self._factory(dict(embedding))
            if not callable(getattr(connector, "embed", None)):
                raise ValueError(
                    f"{embedding.get('provider')} connections cannot embed; "
                    f"agent routing needs an embedding model.")
            self._connectors[key] = connector
        return connector

    # -- what an agent is embedded by ---------------------------------------
    @classmethod
    def texts_for(cls, agent_id: str, agent: Any) -> List[str]:
        manifest = getattr(agent, "manifest", None)
        document = getattr(manifest, "document", None) or {}
        block = document.get("agent") or {}
        name = str(getattr(manifest, "name", "") or block.get("name") or agent_id)
        texts = [f"{name}: {block.get('description') or ''}".strip()]
        tags = [str(t) for t in (block.get("tags") or []) if str(t).strip()]
        if tags:
            texts.append(f"{name} — {', '.join(tags)}")
        for example in (block.get("examples") or [])[: cls.EXAMPLES_MAX]:
            prompt = str((example or {}).get("prompt") or "").strip() if isinstance(example, dict) else str(example or "").strip()
            if prompt:
                texts.append(prompt)
        count = 0
        for tool in document.get("tools") or []:
            for function in tool.get("functions") or []:
                if count >= cls.FUNCTIONS_MAX:
                    break
                line = (f"{name} {tool.get('id') or ''} {function.get('id') or ''}: "
                        f"{function.get('description') or ''}").strip()
                texts.append(line)
                count += 1
        return texts

    @classmethod
    def digest_of(cls, agent_id: str, agent: Any) -> str:
        """What an agent's vectors are filed under: its package digest,
        or a digest of its words for one that has none."""
        digest = str(getattr(agent, "digest", "") or "")
        if digest:
            return digest.replace(":", "_")
        return "text_" + hashlib.sha256(
            "\n".join(cls.texts_for(agent_id, agent)).encode("utf-8")).hexdigest()[:32]

    # -- the index ------------------------------------------------------------
    def _path(self, key: str, digest: str) -> Path:
        return self.cache_dir / key / f"{digest}.json"

    def vectors(self, key: str, digest: str) -> Optional[List[List[float]]]:
        found = self._vectors.get((key, digest))
        if found is not None:
            return found
        path = self._path(key, digest)
        if path.is_file():
            try:
                found = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(found, list) and found:
                    self._vectors[(key, digest)] = found
                    return found
            except (OSError, ValueError):
                pass
        return None

    def indexed(self, agents: Dict[str, Any], embedding: Dict[str, Any]) -> bool:
        key = self.model_key(embedding)
        return all(self.vectors(key, self.digest_of(agent_id, agent)) is not None
                   for agent_id, agent in agents.items())

    async def index(self, agents: Dict[str, Any], embedding: Dict[str, Any]) -> bool:
        """Vectors for every agent that has none yet, in batches, kept
        on disk. True when every agent is indexed afterwards. Nothing
        is kept until every batch has answered: a failure is logged and
        leaves all of them for next time."""
        key = self.model_key(embedding)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            missing = [(agent_id, agent) for agent_id, agent in agents.items()
                       if self.vectors(key, self.digest_of(agent_id, agent)) is None]
            if not missing:
                return True
            try:
                connector = self.connector(embedding)
                texts: List[str] = []
                spans: List[Tuple[str, int, int]] = []
                for agent_id, agent in missing:
                    own = self.texts_for(agent_id, agent)
                    spans.append((self.digest_of(agent_id, agent), len(texts), len(texts) + len(own)))
                    texts.extend(own)
                vectors: List[List[float]] = []
                for start in range(0, len(texts), self.BATCH):
                    vectors.extend(await connector.embed(texts[start: start + self.BATCH]))
                if len(vectors) != len(texts):
                    raise RuntimeError(
                        f"the embedding model answered {len(vectors)} vectors for {len(texts)} texts")
                for digest, start, end in spans:
                    own = vectors[start:end]
                    self._vectors[(key, digest)] = own
                    path = self._path(key, digest)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(own), encoding="utf-8")
                self.logger.info(f"Indexed {len(missing)} agent(s) for routing ({len(texts)} texts)")
                return True
            except Exception as exc:
                self.logger.warning(f"Agent routing index skipped: {exc}")
                return False

    # -- ranking ----------------------------------------------------------------
    @staticmethod
    def cosine(a: List[float], b: List[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na and nb else 0.0

    async def scores(self, agents: Dict[str, Any], text: str,
                     embedding: Dict[str, Any]) -> Optional[Dict[str, float]]:
        """Each agent's closeness to the words, by its closest vector.
        An index that is not whole is built first and the turn waits
        for it, behind any indexing already under way. None when that
        fails or the model will not answer — the caller then lists
        every agent."""
        if not agents:
            return {}
        key = self.model_key(embedding)
        if not self.indexed(agents, embedding):
            if not await self.index(agents, embedding):
                return None
        try:
            query = (await self.connector(embedding).embed([str(text or "")]))[0]
        except Exception as exc:
            self.logger.warning(f"Agent routing skipped this turn: {exc}")
            return None
        scored: Dict[str, float] = {}
        for agent_id, agent in agents.items():
            own = self.vectors(key, self.digest_of(agent_id, agent)) or []
            scored[agent_id] = max((self.cosine(query, v) for v in own), default=0.0)
        return scored

    async def shortlist(self, agents: Dict[str, Any], text: str,
                        opened: Iterable[str], routing: Dict[str, Any],
                        reranker=None) -> Optional[Tuple[List[str], int]]:
        """(ids to list, how many were left off), or None when every
        agent should be listed: under the threshold, or when the
        embedding model could not answer."""
        routing = dict(routing or {})
        embedding = routing.get("embedding")
        threshold = int(routing.get("threshold") or 15)
        if not isinstance(embedding, dict) or len(agents) <= threshold:
            return None
        scored = await self.scores(agents, text, embedding)
        if scored is None:
            return None
        limit = max(1, int(routing.get("shortlist") or 15))
        pool = max(limit, int(routing.get("candidates") or 50))
        ranked = sorted(agents, key=lambda agent_id: (-scored[agent_id], agent_id))
        candidates = ranked[:pool]
        order = candidates
        if reranker is not None and routing.get("rerank", True):
            order = await self.rerank(reranker, text, agents, candidates, limit)
        # The open ones first, always; then the shortlist's worth of
        # the ranked ones that are not already among them.
        listed = [agent_id for agent_id in opened if agent_id in agents]
        room = limit
        for agent_id in order:
            if room <= 0:
                break
            if agent_id not in listed:
                listed.append(agent_id)
                room -= 1
        return listed, len(agents) - len(listed)

    async def rerank(self, connector, text: str, agents: Dict[str, Any],
                     candidates: List[str], limit: int) -> List[str]:
        """The candidates in the order the chat's model puts them for
        this message, the rest after. On any failure, as they came."""
        if not getattr(connector, "reranks", True) or len(candidates) <= 1:
            return list(candidates)
        lines = []
        for agent_id in candidates:
            block = (getattr(getattr(agents[agent_id], "manifest", None), "document", None) or {}).get("agent") or {}
            name = str(getattr(agents[agent_id].manifest, "name", "") or block.get("name") or agent_id)
            description = str(block.get("description") or "")[:160]
            lines.append(f"{agent_id} — {name}: {description}")
        try:
            reply = await connector.chat([
                {"role": "system", "content": Prompts.text("rerank")},
                {"role": "user", "content": Prompts.render(
                    "rerank_ask", message=str(text or ""), limit=str(limit),
                    agents="\n".join(lines))},
            ])  # no cap: a reasoning model spends its reply thinking,
                # and a capped one comes back cut, naming nobody
            chosen = self.parse_ids(str(getattr(reply, "content", "") or ""), candidates)
        except Exception as exc:
            self.logger.warning(f"Agent rerank skipped: {exc}")
            return list(candidates)
        if not chosen:
            return list(candidates)
        return chosen + [agent_id for agent_id in candidates if agent_id not in chosen]

    @staticmethod
    def parse_ids(text: str, known: List[str]) -> List[str]:
        """The ids a reply names, in its order, known ones only."""
        found: List[str] = []
        match = re.search(r"\[.*?\]", text, re.S)
        if match:
            try:
                for item in json.loads(match.group(0)):
                    item = str(item).strip()
                    if item in known and item not in found:
                        found.append(item)
                return found
            except ValueError:
                pass
        for item in re.findall(r"agt_[A-Za-z0-9_]+|[A-Za-z0-9_]+", text):
            if item in known and item not in found:
                found.append(item)
        return found

    async def find(self, agents: Dict[str, Any], query: str,
                   embedding: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """The agents closest to the words, best first — id, name,
        description and closeness — or None when the model will not
        answer. With no words, all of them by name."""
        described = []
        for agent_id, agent in agents.items():
            block = (getattr(getattr(agent, "manifest", None), "document", None) or {}).get("agent") or {}
            described.append({
                "id": agent_id,
                "name": str(getattr(agent.manifest, "name", "") or block.get("name") or agent_id),
                "description": str(block.get("description") or ""),
            })
        if not str(query or "").strip():
            return sorted(described, key=lambda d: d["name"].lower())
        scored = await self.scores(agents, query, embedding)
        if scored is None:
            return None
        for item in described:
            item["closeness"] = round(scored.get(item["id"], 0.0), 3)
        described.sort(key=lambda d: (-d["closeness"], d["name"].lower()))
        return [d for d in described if d["closeness"] > self.FIND_FLOOR][
            :self.FIND_LIMIT]
