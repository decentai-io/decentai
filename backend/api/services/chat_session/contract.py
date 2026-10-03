"""The session contract — one computed answer to every question a chat
session raises, built at the door.

Before this existed, "what may this chat do" was answered in three
places at three times: the settings resolver at create, the scope
builder at socket connect, and the delegation fence per call — and
"which model thinks" could fail for the first time in the middle of a
turn, as a 500 with no sentence attached. The contract moves every one
of those answers to Open, where a missing piece can be a sentence with
a fix in it, and everything downstream READS the one artifact instead
of re-deriving its own.

What it holds:

    llm          the resolved connection block, or None with
                 ``llm_missing`` saying exactly why and where to fix it
    agents       grants ∩ installed ∩ the chat's narrowing, pre-joined:
                 per agent the readable name, version, and the concrete
                 functions this person may call
    permissions  the same authority in the runtime evaluator's shape,
                 served to the runtime as the contract's ``grants``
    skills       the chat's narrowing (None means no narrowing)
    trust        the clamped trust level
    budgets      what the runtime should enforce
    powers       what THIS caller may do in the chat, so the page asks
                 once instead of probing action by action
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from api.services.chat_session.identity import AgentScope
from api.services.chat_session.settings import ChatSettings, ModelChoice, chosen_by
from api.services.chat_session.settings.narrowing import EnabledAgents
from database.stores import AgentGrantStore, AgentManifestStore
from database.stores.settings import LlmConnectionStore
from server.setup.app_state import get_access_controller


class SessionContract:
    """Everything a chat session resolves, asked as one."""

    #: The chat-relevant actions, asked once so the page stops probing.
    POWERS = {
        "send": "ai:chat:sendmessage",
        "approve": "ai:approval:decide",
        "stop": "ai:chat:stop",
        "upload": "ai:chat:uploadfile",
        "configure": "ai:chat:update",
    }

    def of(self, user: Dict[str, Any],
           chat: Dict[str, Any]) -> Dict[str, Any]:
        config = ChatSettings().as_served(chat.get("config"))
        contract: Dict[str, Any] = {
            "routing": self._routing(user),
            "safety": self._safety(user),
            "agents": self._agents(user, config),
            "permissions": AgentScope().of(user)["permissions"],
            "mcp": self._mcp(user),
            "skills": config.get("enabled_skills"),
            "trust": config.get("trust_level"),
            "timezone": config.get("timezone"),
            "budgets": ChatSettings().budgets_of(config),
            "powers": self._powers(user),
        }
        llm, missing = self._llm(user, config)
        contract["llm"] = llm
        if missing:
            contract["llm_missing"] = missing
        return contract

    # ------------------------------------------------------------------
    @staticmethod
    def _mcp(user: Dict[str, Any]) -> list:
        """The person's own MCP servers that are on, with the tools
        they kept on (api/services/data_layer/mcp.py). Where a server
        is and what it is reached with are not here: the runtime asks
        for those when a tool is called (``Mcp:Server:Use``)."""
        from api.services.data_layer.mcp import McpController

        return McpController().serving(user)

    @staticmethod
    def _safety(user: Dict[str, Any]) -> Dict[str, Any]:
        """The part of the organization's Safety setting the runtime
        holds agents to: the sites none may open, and the packages a
        program may install. How often a card is shown is decided here,
        where the cards are, and is not the runtime's to know."""
        from database.stores import OrganizationStore

        safety = OrganizationStore().safety(str(user.get("org_id") or ""))
        return {key: safety[key]
                for key in ("blocked_sites", "packages", "allowed_packages")}

    def _routing(self, user: Dict[str, Any]) -> Dict[str, Any]:
        """The organization's routing numbers, and the embedding model
        as a block the runtime resolves the way it resolves the chat's
        model — provider, model, endpoint, and the ref its key is
        fetched by. ``embedding`` is None when none is chosen, or the
        chosen one is gone: the frame then lists every agent."""
        from database.stores import LlmConnectionStore, OrganizationStore

        routing = dict(OrganizationStore().routing(str(user.get("org_id") or "")))
        ref = routing.pop("embedding_connection_id", "")
        model = str(routing.pop("embedding_model", "") or "")
        embedding = None
        if ref and model:
            doc = LlmConnectionStore().get_in(str(user.get("org_id") or ""), ref)
            if doc is not None:
                embedding = {"provider": doc.get("provider"), "model": model,
                             "secret_ref": doc["_id"]}
                if doc.get("endpoint"):
                    embedding["endpoint"] = doc.get("endpoint")
        routing["embedding"] = embedding
        return routing

    def _llm(self, user: Dict[str, Any], config: Dict[str, Any]):
        """(block, "") or (None, the sentence).

        The chat's own block wins when its connection still resolves —
        checked again HERE, not only when it was written, because a
        connection deleted or unshared since then must fail at the door
        rather than mid-turn. A block with no ref (the scripted
        provider) is served as-is. With no block at all, the person's
        preference and the organization's default answer, exactly as
        creation would have answered."""
        block = config.get("llm")
        if isinstance(block, dict):
            ref = str(block.get("secret_ref") or "").strip()
            if not ref or LlmConnectionStore().visible(user, ref) is not None:
                return dict(block), ""
            return None, (
                "This chat's model connection is no longer available to "
                "you — pick another in the chat's settings."
            )

        resolved = ModelChoice().default(user, chosen_by(user))
        if resolved is not None:
            return resolved, ""
        return None, (
            "No model connection is visible to you. Add one under "
            "Settings, or ask a colleague to share theirs."
        )

    def _agents(self, user: Dict[str, Any],
                config: Dict[str, Any]) -> Dict[str, Any]:
        """agent_ref -> what this person may actually call, pre-joined.

        Three filters meet: the organization's installed set, the grants
        that reach this person, and the chat's own narrowing. A grant of
        ``*`` expands against the manifest, so the page and the model
        read concrete names — and an agent none of whose functions
        survive the join is absent, not present-but-empty."""
        org_id = str(user.get("org_id") or "")
        # A narrowing that names only agents since uninstalled narrows
        # nothing (settings/narrowing.py): a chat born with one would
        # otherwise reach no agent for the rest of its life.
        enabled = EnabledAgents().alive(user, config.get("enabled_agents"))

        granted: Dict[str, Optional[set]] = {}
        for grant in AgentGrantStore().visible(user):
            ref = str(grant.get("agent_ref") or "")
            if not ref:
                continue
            functions = grant.get("functions")
            if functions == AgentGrantStore.ALL_FUNCTIONS:
                granted[ref] = None  # everything the manifest declares
            elif granted.get(ref, set()) is not None:
                granted.setdefault(ref, set()).update(functions or [])

        agents: Dict[str, Any] = {}
        store = AgentManifestStore()
        for doc in store.list(org_id):
            ref = doc["_id"]
            if doc.get("status") != store.STATUS_INSTALLED:
                continue
            if isinstance(enabled, list) and ref not in enabled:
                continue
            if ref not in granted:
                continue

            manifest = doc.get("manifest") or {}
            declared = [
                f"{tool.get('id')}.{function.get('id')}"
                for tool in manifest.get("tools") or []
                for function in tool.get("functions") or []
            ]
            allowed = granted[ref]
            functions = (declared if allowed is None
                         else [name for name in declared if name in allowed])
            if not functions:
                continue

            # The function that shows this agent's screen on request
            # (manifest `watch: true`), named as the chat calls it, so
            # the page knows a browser can be opened here at all.
            watch = next((
                f"{tool.get('id')}.{function.get('id')}"
                for tool in manifest.get("tools") or []
                for function in tool.get("functions") or []
                if function.get("watch") is True
                and f"{tool.get('id')}.{function.get('id')}" in functions), "")
            agents[ref] = {
                "name": (manifest.get("agent") or {}).get("name") or ref,
                "qualified_id": doc.get("qualified_id") or ref,
                "version": doc.get("version", ""),
                "functions": functions,
                **({"watch": f"{ref}.{watch}"} if watch else {}),
                # What the approval names, for a runtime to materialize
                # it by (docs/system/agent-code.md): the package's own
                # id, the digest of its bytes, the hash of its manifest.
                "local_agent_id": str(doc.get("local_agent_id") or ""),
                "package_digest": str(doc.get("package_digest") or ""),
                "manifest_hash": str(doc.get("manifest_hash") or ""),
            }
        return agents

    def _powers(self, user: Dict[str, Any]) -> Dict[str, bool]:
        # A page's question. The runtime acts FOR the person over a
        # fenced surface and never holds these; asking on its behalf
        # would only log a denial per power every time a session builds.
        if user.get("principal_type") == "runtime":
            return {}
        access = get_access_controller()
        return {
            name: bool(access.is_allowed(user, action))
            for name, action in self.POWERS.items()
        }
