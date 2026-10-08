"""The platform executor — the single chokepoint every function invocation
passes through, no matter which reasoning loop proposed it.

Per invocation: resolve (manifest-declared functions only) → ask the
grants whether this chat may reach the function → resolve reference
inputs, apply defaults, validate inputs against the manifest schema →
read the scopes the call names and hold them to the grants → enforce the
permission level against the chat level (pausing for approval when
required) → build the call's context (the mediated resources, and what
else the manifest allows it) → execute in the agent's worker under the
function's timeout → validate the output. Denials, failures, timeouts
and a fault on the platform's own side come back as ``(result,
"error")`` — observations for the proposing loop, never exceptions. A
call cancelled from outside is the one thing raised, after its line is
on the trail.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import time
import uuid
from typing import Any, Callable, Dict, Optional, Tuple

import jsonschema
import referencing

from contracts.record_fields import RecordFields
from contracts.chat import (
    DISPLAYS_PER_CALL_MAX, POST_MAX_CHARS, POSTS_PER_CALL_MAX, agent_source,
    code_asked, display_stored, event_error, CHOICE_MAX_CHARS, CHOICES_MAX,
    QUESTION_MAX_CHARS, SCREEN_FRAME_MAX_BYTES,
)
from ai_runtime.agents.library import InstalledAgent
from ai_runtime.agents.mcp import McpServer
from ai_runtime.sinks import ChatSinks
from ai_runtime.agents.worker_handle import WorkerError
from ai_runtime.agents.confinement import Confinement
from ai_runtime.agents.worker_pool import CallContext, WorkerPool
from ai_runtime.execution.code_grant import CodeGrant
from ai_runtime.execution.pictures import Pictures
from ai_runtime.execution.resources import ResourceAccess
from ai_runtime.runtime_logging import RuntimeLoggerFactory

#: What a frame of a screen carries (contracts/chat.py ScreenFrame).
#: Nothing else an agent sends beside a frame is passed on.
SCREEN_FRAME_FIELDS = ("image_base64", "mime", "width", "height", "frame",
                       "taken", "tabs")


# ----------------------------------------------------------------------
# The approval binding: sha256 over sorted, compact JSON of (agent,
# function, inputs) — stored on the approval record and inside the
# turn checkpoint, recomputed and compared before any resumed
# dispatch, so what executes after a park is provably what the
# person approved.
# ----------------------------------------------------------------------
def action_hash(agent_id: str, function: str, inputs: Dict[str, Any]) -> str:
    payload = json.dumps(
        {"agent_id": agent_id, "function": function, "inputs": inputs},
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ParkedInvocation:
    """The one invocation waiting on the human, inputs already resolved
    and defaulted — literally what will run if approved."""

    def __init__(self, agent_id: str, function: str,
                 inputs: Dict[str, Any], permission_level: int,
                 chat_level: int):
        self.agent_id = agent_id
        self.function = function
        self.inputs = inputs
        self.permission_level = permission_level
        self.chat_level = chat_level

    def hash(self) -> str:
        return action_hash(self.agent_id, self.function, self.inputs)


class FunctionExecutor:
    """One executor per chat: it carries the chat's resource provider,
    its grants, and the chat's doors (ai_runtime/sinks.py) — the
    approval card, the asks, the trail. The chat level rides on each
    invocation because the user can change it mid-conversation.
    """

    DEFAULT_TIMEOUT_SECONDS = 60

    def __init__(
        self,
        provider=None,
        sinks: Optional[ChatSinks] = None,
        grants=None,
        workers: Optional[WorkerPool] = None,
        conversation: str = "",
        safety: Optional[Dict[str, Any]] = None,
    ):
        self.provider = provider
        #: The chat's doors, by name (ai_runtime/sinks.py): what a call
        #: may ask of the chat, and what is written about it. A door
        #: that is None has nobody behind it — a test, or a fire from
        #: the clock — and each use says what that means for it: no
        #: approve is a denial, no ask is a refusal, no audit is a
        #: trail with nothing on it.
        self.sinks = sinks or ChatSinks()
        #: The chat these invocations run in, as an opaque key handed to
        #: every call (call.conversation): what lets a function keep a
        #: browser open between calls for one chat and no other.
        self.conversation = str(conversation or "")
        # The pool the invocations run in. Pass the process's shared
        # pool so workers stay warm across chats; absent, the executor
        # lazily owns a private one — the test-and-standalone path.
        self.workers = workers
        #: What the deployment lets agents do (Settings:Safety), as the
        #: chat's contract carries it: the sites no agent may open, and
        #: the packages a program may install where a list is kept.
        self.safety: Dict[str, Any] = dict(safety or {})
        #: call_id -> the hosts that call's worker connected to, from
        #: the call's end until its line on the trail is written.
        self._reached: Dict[str, Dict[str, int]] = {}
        #: call_id -> whose screen it is, for every call that has shown
        #: a frame and not said it closed. A screen is a call's: when
        #: the call ends, however it ends, whoever is watching is told.
        self._screens: Dict[str, Dict[str, Any]] = {}
        # FunctionGrants. None is unrestricted and is a test's: a
        # session's executor and a fire's carry the delegation's
        # grants, so denied by default applies. (The clock's own
        # executor in server/app.py has none; a fire runs with the
        # host's, fire_context, and not with that one.)
        self.grants = grants

        self.logger = RuntimeLoggerFactory.get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------
    async def invoke(
        self,
        agent: InstalledAgent,
        canonical_name: str,
        inputs: Dict[str, Any],
        chat_level: int = 1,
        call_id: str = "",
    ) -> Tuple[Dict[str, Any], str]:
        """The gates, then the execution — and either way, the trail.
        ``call_id`` names the call for everything said about it — the
        caller's narration and the agent's own progress; minted here
        when the caller brings none."""
        started = time.monotonic()
        call_id = call_id or f"c_{uuid.uuid4().hex[:12]}"
        try:
            result, status = await self._invoke(agent, canonical_name, inputs,
                                                chat_level, call_id)
        except asyncio.CancelledError:
            # Stopped from outside. The call still happened, as far as
            # it got, and the trail says so before the stop goes on.
            await self._witness_cancelled(
                agent, canonical_name, inputs, chat_level, started,
                reached=self._reached.pop(call_id, None))
            raise
        except Exception as exc:
            result, status = self._broke(canonical_name, exc)
        await self._record(agent, canonical_name, inputs, chat_level,
                           result, status, started,
                           reached=self._reached.pop(call_id, None))
        return result, status

    def _broke(self, canonical_name: str,
               exc: Exception) -> Tuple[Dict[str, Any], str]:
        """Something on the platform's own side raised while a call was
        made. It ends as every call ends — a result, and a line on the
        trail — and not as an exception in whoever asked."""
        self.logger.error(
            f"{canonical_name}: the call could not be completed: "
            f"{type(exc).__name__}: {exc}", exc_info=True)
        return {"error": (
            f"'{canonical_name}' could not be completed: the platform "
            f"failed while running it ({type(exc).__name__}). Whether it "
            f"took effect is not known."
        )}, "error"

    async def _witness_cancelled(self, agent: InstalledAgent,
                                 canonical_name: str, inputs: Any,
                                 chat_level: int, started: float,
                                 resumed: bool = False,
                                 reached: Optional[Dict[str, int]] = None,
                                 ) -> None:
        try:
            await asyncio.shield(self._record(
                agent, canonical_name, inputs, chat_level,
                {"error": f"'{canonical_name}' was cancelled"}, "error",
                started, resumed=resumed, reached=reached))
        except asyncio.CancelledError:
            pass  # cancelled again: the record goes on, shielded
        except Exception as exc:
            self.logger.warning(f"Cancelled call not recorded: {exc}")

    async def _invoke(
        self,
        agent: InstalledAgent,
        canonical_name: str,
        inputs: Dict[str, Any],
        chat_level: int = 1,
        call_id: str = "",
    ) -> Tuple[Dict[str, Any], str]:
        # The name arrives as the platform writes it (the approval's
        # ref); the manifest and the worker know the package's own
        # (agents/approved.py). Grants, the trace and every message
        # keep the platform's.
        declared = agent.manifest.function(agent.declared(canonical_name))
        if declared is None:
            return {"error": f"Unknown function '{canonical_name}'"}, "error"
        _, function_spec = declared

        if self.grants is not None and not self.grants.may_reach(canonical_name):
            return {
                "error": (
                    f"'{canonical_name}' is not permitted for this chat — "
                    f"the delegation does not grant it."
                ),
                "not_permitted": True,
            }, "error"

        # Reference inputs resolve to their stored values BEFORE schema
        # validation — the function sees real data, verified end to end.
        if self.sinks.read_result is not None:
            inputs, reference_error = await self._resolve_references(inputs)
            if reference_error:
                return {
                    "error": "Reference input could not be resolved: "
                             + reference_error,
                }, "error"

        inputs = self._apply_defaults(function_spec.get("inputs") or {}, inputs)
        if not self._sayable(inputs):
            # NaN and Infinity are numbers to Python and to no schema,
            # store or trail: a model's reply may hold one, and a call
            # may not.
            return {"error": "Invalid inputs: a number that is not one "
                             "(NaN or Infinity)."}, "error"
        schema_error = self._validate(function_spec.get("inputs"), inputs)
        if schema_error:
            return {"error": f"Invalid inputs: {schema_error}"}, "error"

        scopes = self._scope_values(agent, function_spec, inputs)
        unjudged = self._scope_unjudged(scopes)
        if unjudged:
            return {"error": (
                f"'{canonical_name}' must be told one {unjudged}, as a "
                f"single value: what a call acts on is judged by name."
            )}, "error"
        unnamed = self._scope_unnamed(function_spec, scopes)
        if unnamed:
            return {"error": (
                f"'{canonical_name}' must be told its {unnamed}: what a "
                f"call acts on has to be named for it to be permitted."
            )}, "error"
        if self.grants is not None and not self.grants.allows(
            canonical_name, scopes
        ):
            return self._out_of_scope(canonical_name, scopes)

        level = int(function_spec["permission_level"])
        if level > chat_level:
            approved = await self._request_approval(
                agent, canonical_name, level, chat_level, inputs
            )
            if approved is None:
                return {"error": (
                    f"'{canonical_name}' needs the person's approval, and "
                    f"they could not be asked just now. Nothing was run, "
                    f"and nobody refused it."
                )}, "error"
            if not approved:
                return self._denial(canonical_name, level)

        return await self._execute(
            agent, function_spec, canonical_name, inputs, call_id
        )

    @staticmethod
    def _scope_values(
        agent: InstalledAgent,
        function_spec: Dict[str, Any],
        inputs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """What this call is about, in the agent's own vocabulary.

        The manifest binds each scope to one of the function's inputs and
        says how the value is normalized; a policy constrains which values
        the caller is entitled to. Reading them here — after defaults and
        validation — means the value judged is read from the inputs the
        function runs with. It is judged as a policy names it and not
        always as it was given: normalized the way the manifest says,
        and empty text as no value named. The function is handed its
        input as given.

        Every scope the function declares is in the answer: one whose
        input this call did not give is there as None, so a policy can
        tell "no value was named" from "this function has no such
        scope" (grants.py reads the first against the caller).
        """
        declared = (function_spec.get("authorization") or {}).get("scopes") or {}
        vocabulary = agent.manifest.scopes

        values: Dict[str, Any] = {}
        for name, binding in declared.items():
            source = (binding or {}).get("from_input")
            value = inputs.get(source) if source else None
            # Empty text names nothing, exactly as leaving the input
            # out does — and is judged as that. Read as a value it
            # would be one no deny lists, while a function is free to
            # take it to mean "all of them".
            if isinstance(value, str) and not value.strip():
                value = None
            if value is None:
                values[name] = None
                continue
            normalization = (vocabulary.get(name) or {}).get("normalization")
            if isinstance(value, str) and normalization == "lowercase":
                value = value.lower()
            elif isinstance(value, str) and normalization == "uppercase":
                value = value.upper()
            values[name] = value
        return values

    @staticmethod
    def _scope_unjudged(scopes: Dict[str, Any]) -> str:
        """The first scope whose value is not one value — a list, an
        object — or ''. A policy names values; several at once cannot
        be held to it."""
        for name, value in scopes.items():
            if value is not None and not isinstance(
                    value, (str, int, float, bool)):
                return name
        return ""

    @staticmethod
    def _sayable(value: Any) -> bool:
        try:
            json.dumps(value, allow_nan=False, default=str)
        except ValueError:
            return False
        return True

    @staticmethod
    def _scope_unnamed(function_spec: Dict[str, Any],
                       scopes: Dict[str, Any]) -> str:
        """The first scope the manifest requires a value for and this
        call gave none, or ''. Required is the manifest's default; a
        function says ``required: false`` where it has a sensible
        answer of its own."""
        declared = (function_spec.get("authorization") or {}).get("scopes") or {}
        for name, binding in declared.items():
            if (binding or {}).get("required", True) and scopes.get(name) is None:
                return name
        return ""

    @staticmethod
    def _out_of_scope(
        canonical_name: str, scopes: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], str]:
        """Refused for these values, and says which — a model told only
        "not permitted" will retry the same call forever."""
        described = ", ".join(f"{name}={value}" for name, value in scopes.items()
                              if value is not None)
        return {
            "error": (
                f"'{canonical_name}' is not permitted for {described or 'this'} "
                f"— the delegation grants it for other values only."
            ),
            "not_permitted": True,
        }, "error"

    def _llm_for(self, access: ResourceAccess) -> Optional[Callable]:
        """The chat's model for one call, with pictures resolved here.
        An agent names a picture (``{resource_id, ref}``); the bytes are
        read under this call's own file grant — a function shows the
        model only what its manifest lets it read — and handed on as
        ``{mime, content_base64}`` to whatever serves the model. A
        picture the agent carries itself is taken too, and either way
        it is checked to be one (pictures.py)."""
        if self.sinks.llm is None:
            return None
        serve = self.sinks.llm

        async def complete(messages, max_tokens=None, images=None):
            pictures = []
            if len(images or []) > Pictures.MAX_PER_ASK:
                raise WorkerError(f"The model can be shown at most "
                                  f"{Pictures.MAX_PER_ASK} pictures at once.")
            for named in images or []:
                if not isinstance(named, dict):
                    continue
                if named.get("content_base64"):
                    # Carried, not named: the agent's word for what it
                    # is counts for nothing until the bytes agree.
                    pictures.append(self._picture(
                        "it", named.get("mime"), named.get("content_base64")))
                    continue
                record = await access.read_file(
                    str(named.get("resource_id") or ""),
                    str(named.get("ref") or ""))
                mime = str(record.get("file_type") or "") or (
                    mimetypes.guess_type(str(record.get("filename") or ""))[0] or "")
                encoded = str(record.get("content_base64") or "")
                pictures.append(self._picture(
                    f"'{named.get('ref')}'", mime, encoded))
            if pictures:
                return await serve(messages, max_tokens, pictures)
            return await serve(messages, max_tokens)
        return complete

    @staticmethod
    def _picture(name: str, mime: Any, encoded: Any) -> Dict[str, str]:
        try:
            return Pictures.checked(mime, encoded)
        except ValueError as why:
            raise WorkerError(
                f"{name} is not an image the model can be shown: {why}.")

    @classmethod
    def _refs_in(cls, value: Any) -> set:
        """Every string in a call's inputs: the refs it was handed — a
        file the person attached, one another agent stored — which it
        may read wherever they are kept (ResourceAccess.handed)."""
        if isinstance(value, str):
            return {value}
        if isinstance(value, dict):
            return set().union(*(cls._refs_in(v) for v in value.values()))
        if isinstance(value, (list, tuple)):
            return set().union(*(cls._refs_in(v) for v in value))
        return set()

    def _ask_for(self, agent: InstalledAgent, canonical_name: str,
                 call_id: str,
                 access: Optional[ResourceAccess] = None) -> Optional[Callable]:
        """The agent asking the person, checked here (words, length,
        choices), then handed to the chat as a card in the agent's name.
        None where there is no one to ask."""
        if self.sinks.ask is None:
            return None
        source = agent_source(agent.agent_id, agent.manifest.name,
                              canonical_name, call_id=call_id)

        async def ask(question: str, choices: list,
                      expects: str = "") -> Optional[str]:
            question = str(question or "").strip()
            if not question:
                raise WorkerError("A question needs words.")
            expects = str(expects or "").strip().lower()
            if expects not in ("", "text", "file"):
                raise WorkerError("A question expects 'text' or 'file'.")
            if len(question) > QUESTION_MAX_CHARS:
                raise WorkerError(f"A question may be at most "
                                  f"{QUESTION_MAX_CHARS} characters.")
            offered = list(dict.fromkeys(
                str(c).strip() for c in choices or [] if str(c).strip()))
            if len(offered) > CHOICES_MAX:
                raise WorkerError(f"A question may offer at most "
                                  f"{CHOICES_MAX} choices.")
            if any(len(c) > CHOICE_MAX_CHARS for c in offered):
                raise WorkerError(f"A choice may be at most "
                                  f"{CHOICE_MAX_CHARS} characters.")
            if expects == "file":
                # Buttons answer with words; a file request has none.
                # The file the person gives is handed to this call.
                answer = await self.sinks.ask(question, [], source, expects="file")
                if access is not None:
                    access.hand(answer)
                return answer
            return await self.sinks.ask(question, offered, source)
        return ask

    def _propose_for(self, agent: InstalledAgent, canonical_name: str,
                     call_id: str,
                     grant: Optional[CodeGrant] = None) -> Optional[Callable]:
        """Code the agent wants to run, checked here against the card's
        own shape (contracts/chat.py CodeAsk), then handed to the chat
        to be read and put before the person. None where there is no
        one to ask.

        ``grant`` is there for a function that declared ``code: true``:
        what a card it is allowed names is then opened and installable
        for this call (code_grant.py), so the names are checked before
        the card is shown."""
        if self.sinks.propose is None:
            return None
        source = agent_source(agent.agent_id, agent.manifest.name,
                              canonical_name, call_id=call_id)

        async def propose(code: Any) -> Optional[bool]:
            if not isinstance(code, dict):
                raise WorkerError("A proposal is code, its purpose and "
                                  "what it needs.")
            # A review is the platform's to write, never the agent's.
            asked, why = code_asked(
                {key: value for key, value in code.items() if key != "review"})
            if asked is None:
                raise WorkerError(f"The code could not be proposed: {why}")
            if grant is not None:
                why = CodeGrant.problem(asked, self._listed_packages())
                if why:
                    raise WorkerError(f"The code could not be proposed: {why}")
            allowed = await self.sinks.propose(asked, source)
            if allowed is True and grant is not None:
                grant.allow(asked)
            return allowed
        return propose

    def _listed_packages(self) -> Optional[list]:
        """The packages a program may install here, or None where the
        deployment keeps no list."""
        if self.safety.get("packages") != "listed":
            return None
        return [str(name) for name in self.safety.get("allowed_packages") or []]

    def _install_for(self, agent: InstalledAgent,
                     grant: CodeGrant) -> Callable:
        """Packages for code the person allowed: exactly the ones a
        card of this call named, installed beside the agent's own
        environment by the platform — downloaded and built where that
        is confined, and never by the code that asked."""
        async def install(packages: Any) -> str:
            names = [str(p).strip() for p in packages or [] if str(p).strip()] \
                if isinstance(packages, list) else []
            if not names:
                raise WorkerError("Name the packages to install.")
            refused = grant.allowed(names)
            if refused is not None:
                raise WorkerError(
                    f"'{refused}' is not on a card the person allowed: put "
                    f"the code that needs it before them first.")
            extras = getattr(agent.environment, "extras", None)
            if extras is None:
                raise WorkerError("Packages cannot be installed here.")
            folder, errors = await asyncio.get_running_loop().run_in_executor(
                None, extras, names, Confinement.place_for_building())
            if errors:
                raise WorkerError("; ".join(errors))
            return str(folder)
        return install

    #: What one ask may carry (contracts/chat.py CredentialAsk keeps the
    #: same bounds on the card).
    CREDENTIAL_FIELDS_MAX = 20

    def _credential_for(self, agent: InstalledAgent, canonical_name: str,
                        call_id: str) -> Optional[Callable]:
        """A login the agent asks for as it works, checked here for
        shape, then handed to the chat to resolve through the person's
        cards in the agent's name. None where there is no one to ask."""
        if self.sinks.credential is None:
            return None
        source = agent_source(agent.agent_id, agent.manifest.name,
                              canonical_name, call_id=call_id)

        async def credential(host: str, fields: list, account, site, refresh):
            host = str(host or "").strip()
            if not host or len(host) > 253:
                raise WorkerError("A login needs the host of the page asking for it.")
            if not isinstance(fields, list) or not fields \
                    or len(fields) > self.CREDENTIAL_FIELDS_MAX:
                raise WorkerError(f"A login asks for 1 to "
                                  f"{self.CREDENTIAL_FIELDS_MAX} fields.")
            cleaned = []
            for field in fields:
                if not isinstance(field, dict) or not str(field.get("name") or "").strip():
                    raise WorkerError("Each field needs a name.")
                kind = str(field.get("type") or "text").strip().lower()
                if kind not in ("text", "secret"):
                    raise WorkerError("A field's type is text or secret.")
                cleaned.append({
                    "name": str(field["name"]).strip().lower()[:60],
                    "label": str(field.get("label") or field["name"]).strip()[:80],
                    "type": kind,
                    "required": field.get("required", True) is not False,
                    "remember": field.get("remember", True) is not False,
                })
            return await self.sinks.credential(
                host, cleaned, str(account or "")[:200] or None,
                str(site or "")[:200] or None, bool(refresh), source)
        return credential

    def _screen_for(self, agent: InstalledAgent, canonical_name: str,
                    call_id: str) -> Optional[Callable]:
        """A screen the function shows, in the agent's name, to whoever
        is watching the chat. None where nobody is."""
        if self.sinks.screen is None:
            return None
        source = agent_source(agent.agent_id, agent.manifest.name,
                              canonical_name, call_id=call_id)

        async def screen(kind: str, params: Dict[str, Any]) -> None:
            if kind != "frame":
                self._screens.pop(call_id, None)
                await self.sinks.screen(kind, {"call_id": call_id}, source)
                return
            # What reaches a person's browser is a frame by the chat's
            # contract and a picture by its own bytes; anything else an
            # agent sent as one is dropped, and the function runs on.
            frame = {name: params[name] for name in SCREEN_FRAME_FIELDS
                     if name in params}
            frame["call_id"] = call_id
            if event_error({"event": "screen_frame", **frame}) or Pictures.problem(
                    frame.get("mime", "image/jpeg"), frame.get("image_base64"),
                    Pictures.FRAME_TYPES):
                return
            # A frame is small because many are sent, to every browser
            # that watches. The SDK keeps to the size; an agent that
            # does not use it is held to it here.
            if len(frame["image_base64"]) > SCREEN_FRAME_MAX_BYTES * 4 // 3 + 4:
                return
            self._screens[call_id] = source
            await self.sinks.screen(kind, frame, source)
        return screen

    async def _end_screen(self, call_id: str) -> None:
        """A call is over: a screen it showed and never closed is
        closed for it. A function that returned, raised, ran out of
        time or lost its worker says nothing of its screen, and a
        picture left standing reads as something still running."""
        source = self._screens.pop(call_id, None)
        if source is None or self.sinks.screen is None:
            return
        try:
            await self.sinks.screen("closed", {"call_id": call_id}, source)
        except Exception as exc:
            self.logger.warning(f"A screen could not be told closed: {exc}")

    async def screen_input(self, call_id: str, events: list) -> bool:
        """The person acting on a screen a running call shows."""
        return await self._pool().screen_input(call_id, events)

    def _post_for(self, agent: InstalledAgent, canonical_name: str,
                  call_id: str, displays: list) -> Optional[Callable]:
        """The agent speaking for itself: checked here (words, length,
        how many, displays this call offered and no other), then handed
        to the chat as a message in the agent's name. None where there
        is no chat to speak in."""
        if self.sinks.post is None:
            return None
        source = agent_source(agent.agent_id, agent.manifest.name,
                              canonical_name, call_id=call_id)
        posted: list = []

        async def post(text: str, display_ids: list) -> bool:
            text = str(text or "").strip()
            if not text:
                raise WorkerError("A post needs words.")
            if len(text) > POST_MAX_CHARS:
                raise WorkerError(f"A post may be at most {POST_MAX_CHARS} "
                                  f"characters.")
            if len(posted) >= POSTS_PER_CALL_MAX:
                raise WorkerError(f"A call may post at most "
                                  f"{POSTS_PER_CALL_MAX} times.")
            offered = {d["display_id"]: d for d in displays}
            parts = []
            for display_id in display_ids:
                display = offered.get(display_id)
                if display is None:
                    raise WorkerError(f"'{display_id}' is not a display this "
                                      f"call offered.")
                chart = display.get("kind") == "chart"
                parts.append({
                    "type": "graph" if chart else "table",
                    "text": display.get("title") or ("Chart" if chart else "Table"),
                    "storage_ref": display_id, "source": source,
                })
            posted.append(text)
            return bool(await self.sinks.post(text, source, parts))
        return post

    def _show_for(self, canonical_name: str,
                  displays: list) -> Optional[Callable]:
        """What the call offers to show, checked and kept: its shape and
        size against the chat contract, its rows in chat storage — the
        display's id is its storage ref. Nothing here reaches a person:
        the assistant's say.show does, and only for a successful call.
        None where nobody could see it (no storage: the clock, tests)."""
        if self.sinks.store is None:
            return None

        # Displays on their way to the store: counted with those kept,
        # so a dozen offered at once are held to the same limit.
        storing = [0]

        async def show(spec: Dict[str, Any]) -> Optional[str]:
            if len(displays) + storing[0] >= DISPLAYS_PER_CALL_MAX:
                raise WorkerError(f"A call may offer at most "
                                  f"{DISPLAYS_PER_CALL_MAX} displays.")
            stored, problem = display_stored(spec)
            if problem:
                raise WorkerError(f"Display refused: {problem}")
            storing[0] += 1
            try:
                storage_ref = await self.sinks.store(canonical_name, stored)
            except Exception as exc:
                self.logger.warning(f"Display storage failed: {exc}")
                storage_ref = None
            finally:
                storing[0] -= 1
            if not storage_ref:
                # Nowhere to keep it (a fire with no chat) or the store
                # failed and said so above: the offer is simply not
                # there to show, and the function runs on.
                return None
            displays.append({
                "display_id": storage_ref, "kind": str(spec.get("kind")),
                "title": str(spec.get("title") or ""),
            })
            return storage_ref
        return show

    def _progress_for(self, agent: InstalledAgent, canonical_name: str,
                      call_id: str) -> Optional[Callable]:
        """The agent's own narration, told with who is speaking: the
        worker says a line, the chat hears it as this agent's, on this
        call. None when nobody listens (the clock, tests)."""
        if self.sinks.progress is None:
            return None
        source = agent_source(agent.agent_id, agent.manifest.name,
                              canonical_name, call_id=call_id)

        async def progress(description: str) -> None:
            text = str(description or "").strip()
            if text:
                await self.sinks.progress(text, source)
        return progress

    async def _execute(
        self,
        agent: InstalledAgent,
        function_spec: Dict[str, Any],
        canonical_name: str,
        inputs: Dict[str, Any],
        call_id: str = "",
    ) -> Tuple[Dict[str, Any], str]:
        """Everything past the gates: the invocation in the agent's
        worker, output validation, and result storage.

        The context IS the invocation's authority — the mediated
        resources built from the function's declared operations, and
        the model only when the manifest declares ``llm: true``. The
        worker holds none of it; every ask resolves back here, per
        call_id, against this context (worker_pool.py)."""
        call_id = call_id or f"c_{uuid.uuid4().hex[:12]}"
        # Before anything runs: a function whose declared outputs are
        # not a schema would do its work, and its writes, and then fail.
        for part in ("inputs", "outputs"):
            unreadable = self._schema_problem(function_spec.get(part))
            if unreadable:
                return {"error": (
                    f"'{canonical_name}' cannot run: its manifest's {part} "
                    f"are not a schema ({unreadable})."
                )}, "error"
        displays: list = []
        # Declared or nothing: only a function that said it runs code
        # is opened what a card names.
        grant = CodeGrant() if function_spec.get("code") is True else None
        access = ResourceAccess(
            self._grants(function_spec),
            self.provider,
            definitions=self._definitions(agent),
            namespace=agent.agent_id,
            handed=self._refs_in(inputs),
            fields={
                resource["id"]: RecordFields.declared(resource)
                for resource in agent.manifest.resources("data")
            },
            constraints={
                resource["id"]: resource["constraints"]
                for resource in agent.manifest.resources("files")
                if isinstance(resource.get("constraints"), dict)
            },
        )
        context = CallContext(
            resources=access,
            # Declared or nothing: an undeclared function cannot reach
            # the model even when the executor holds one.
            llm=self._llm_for(access) if function_spec.get("llm") is True else None,
            progress=self._progress_for(agent, canonical_name, call_id),
            show=self._show_for(canonical_name, displays),
            post=self._post_for(agent, canonical_name, call_id, displays),
            ask=self._ask_for(agent, canonical_name, call_id, access),
            propose=self._propose_for(agent, canonical_name, call_id, grant),
            install=self._install_for(agent, grant) if grant is not None else None,
            blocked=self.safety.get("blocked_sites"),
            # Declared or nothing: a function that did not say it may
            # ask for logins cannot, whoever is there to answer.
            credential=(self._credential_for(agent, canonical_name, call_id)
                        if function_spec.get("credentials") is True else None),
            screen=self._screen_for(agent, canonical_name, call_id),
        )
        if grant is not None:
            grant.context = context

        timeout = int(
            function_spec.get("timeout_seconds") or self.DEFAULT_TIMEOUT_SECONDS
        )
        try:
            if isinstance(agent, McpServer):
                # A tool of a server the person added: it runs there,
                # not in a worker here (agents/mcp.py). Past the same
                # gates, and held to the same list of blocked sites.
                blocked = self._blocked(agent.host)
                if blocked:
                    return {"error": blocked}, "error"
                context.reached[agent.host] = 1
                result, status = await asyncio.wait_for(
                    agent.run(agent.declared(canonical_name), inputs), timeout)
            else:
                result, status = await self._pool().invoke(
                    agent, call_id,
                    agent.declared(canonical_name), inputs, context, timeout,
                    conversation=self.conversation,
                )
        except asyncio.TimeoutError:
            return {
                "error": f"'{canonical_name}' timed out after {timeout}s"
            }, "error"
        except WorkerError as exc:
            # The WORKER failed, not the function — a crashed process, a
            # broken handshake. The distinction matters to whoever reads
            # the observation: retrying the same call may well work.
            self.logger.error(f"{canonical_name}: worker failed: {exc}")
            return {
                "error": f"'{canonical_name}' failed: {str(exc).strip()[:300]}"
            }, "error"
        finally:
            # What the person opened, they opened for this call.
            if grant is not None:
                grant.close()
            if context.reached:
                self._reached[call_id] = dict(context.reached)
            await self._end_screen(call_id)

        # The keys the platform writes on a result are read as its own
        # word by everything after this — the trail, the evidence, the
        # page. A function that wrote one itself has it taken out.
        if isinstance(result, dict):
            result = {key: value for key, value in result.items()
                      if key not in self.PLATFORM_KEYS}

        if status == "success":
            output_error = self._validate(function_spec.get("outputs"), result)
            if output_error:
                return {
                    "error": (
                        f"'{canonical_name}' returned output not matching "
                        f"its manifest: {output_error}"
                    ),
                }, "error"

            # Record the verified result. Storage is audit-plus-reference,
            # not authority: a failed write logs and the result stands.
            if self.sinks.store is not None:
                try:
                    storage_ref = await self.sinks.store(canonical_name, result)
                except Exception as exc:
                    self.logger.warning(f"Result storage failed: {exc}")
                    storage_ref = None
                if storage_ref:
                    result = {**result, "storage_ref": storage_ref}
            # What the call offered to show rides its result — added, like
            # the storage ref, after the output was proved.
            if displays:
                result = {**result, "displays": list(displays)}

        return result, status

    def _blocked(self, host: str) -> str:
        """Why this host may not be reached, by the deployment's list of
        sites no agent may open — or ''. A name covers every host under
        it."""
        host = str(host or "").lower().rstrip(".")
        for name in self.safety.get("blocked_sites") or []:
            name = str(name or "").lower().strip(".")
            if name and (host == name or host.endswith(f".{name}")):
                return (f"{host} is on the list of sites nothing may open "
                        f"here.")
        return ""

    def _pool(self) -> WorkerPool:
        if self.workers is None:
            self.workers = WorkerPool()
        return self.workers

    # ------------------------------------------------------------------
    # The trail
    # ------------------------------------------------------------------

    #: What only the platform writes on a call's result: where it was
    #: kept, what it offered to show, and how a gate refused it.
    PLATFORM_KEYS = ("storage_ref", "displays", "denied", "not_permitted")

    #: How many hosts one call's line on the trail names.
    REACHED_MAX = 20

    OUTLINE_STRING_MAX = 200
    OUTLINE_ITEMS_MAX = 20
    OUTLINE_MAX_CHARS = 2000

    async def _record(
        self,
        agent: InstalledAgent,
        canonical_name: str,
        inputs: Dict[str, Any],
        chat_level: int,
        result: Any,
        status: str,
        started: float,
        resumed: bool = False,
        reached: Optional[Dict[str, int]] = None,
    ) -> None:
        """One event for the platform's trail, whatever the outcome:
        a call that ran, one the gates refused, one the person denied.
        ``reached`` is where the call's worker connected while it ran,
        as the proxy counted it: the names, never what was sent."""
        if self.sinks.audit is None:
            return
        declared = agent.manifest.function(agent.declared(canonical_name))
        outcome = status
        if status != "success" and isinstance(result, dict):
            if result.get("denied"):
                outcome = "denied"
            elif result.get("not_permitted"):
                outcome = "refused"
        event: Dict[str, Any] = {
            "event_type": "execution",
            "agent_id": agent.agent_id,
            "agent_name": agent.manifest.name,
            "function": canonical_name,
            "chat_level": int(chat_level),
            "status": outcome,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "inputs": self._outline(inputs),
        }
        if declared is not None:
            event["permission_level"] = int(declared[1].get("permission_level") or 0)
        if resumed:
            event["resumed"] = True
        if reached:
            # The busiest first. A browser loads a page from dozens of
            # hosts: the line keeps the first of them and counts the rest.
            hosts = sorted(reached.items(), key=lambda item: (-item[1], item[0]))
            event["reached"] = [{"host": host, "connections": count}
                                for host, count in hosts[: self.REACHED_MAX]]
            if len(hosts) > self.REACHED_MAX:
                event["reached_more"] = len(hosts) - self.REACHED_MAX
        if isinstance(result, dict):
            if status != "success" and result.get("error"):
                event["error"] = str(result["error"])[:500]
            if isinstance(result.get("storage_ref"), str):
                event["storage_ref"] = result["storage_ref"]
        try:
            await self.sinks.audit(event)
        except Exception as exc:
            self.logger.warning(f"Execution not recorded on the trail: {exc}")

    @classmethod
    def _outline(cls, value: Any, depth: int = 0) -> Any:
        """The inputs, small enough to keep forever: long strings cut,
        long lists counted, and the whole bounded — an outline of what
        was asked, never a copy of a document."""
        if depth == 0:
            outlined = cls._outline(value, 1)
            if len(json.dumps(outlined, default=str)) > cls.OUTLINE_MAX_CHARS:
                keys = sorted(value.keys()) if isinstance(value, dict) else []
                return {"_truncated": True, "keys": keys[: cls.OUTLINE_ITEMS_MAX]}
            return outlined
        if isinstance(value, str):
            return value if len(value) <= cls.OUTLINE_STRING_MAX \
                else value[: cls.OUTLINE_STRING_MAX] + "…"
        if isinstance(value, dict):
            if depth > 4:
                return {"_keys": len(value)}
            return {str(k): cls._outline(v, depth + 1) for k, v in list(value.items())[: cls.OUTLINE_ITEMS_MAX]}
        if isinstance(value, list):
            if depth > 4:
                return {"_items": len(value)}
            shown = [cls._outline(v, depth + 1) for v in value[: cls.OUTLINE_ITEMS_MAX]]
            if len(value) > cls.OUTLINE_ITEMS_MAX:
                shown.append({"_more": len(value) - cls.OUTLINE_ITEMS_MAX})
            return shown
        if isinstance(value, float) and (
                value != value or value in (float("inf"), float("-inf"))):
            return str(value)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return str(value)[: cls.OUTLINE_STRING_MAX]

    async def resume_invoke(
        self,
        agent: InstalledAgent,
        parked: ParkedInvocation,
        expected_hash: str,
        decision: str,
        repark: Optional[Callable] = None,
    ) -> Tuple[Dict[str, Any], str]:
        """A parked invocation completed on a fresh connection — the
        gates again, then the trail."""
        started = time.monotonic()
        call_id = f"c_{uuid.uuid4().hex[:12]}"
        try:
            result, status = await self._resume_invoke(
                agent, parked, expected_hash, decision, repark, call_id)
        except asyncio.CancelledError:
            await self._witness_cancelled(
                agent, parked.function, parked.inputs, parked.chat_level,
                started, resumed=True,
                reached=self._reached.pop(call_id, None))
            raise
        except Exception as exc:
            result, status = self._broke(parked.function, exc)
        await self._record(agent, parked.function, parked.inputs,
                           parked.chat_level, result, status, started,
                           resumed=True,
                           reached=self._reached.pop(call_id, None))
        return result, status

    async def _resume_invoke(
        self,
        agent: InstalledAgent,
        parked: ParkedInvocation,
        expected_hash: str,
        decision: str,
        repark: Optional[Callable] = None,
        call_id: str = "",
    ) -> Tuple[Dict[str, Any], str]:
        """A parked invocation completed on a fresh connection.

        Every gate re-runs against CURRENT state: the grants of this
        connection's scope, the recomputed action hash binding the
        approved inputs to what executes, and the current manifest's
        input schema. References were resolved and defaults applied
        before the original park, so neither runs again — the hash
        pins those exact values.

        ``decision`` is the backend's verdict: approve, deny, expired,
        or pending — pending re-parks via ``repark`` (an async () ->
        bool for the remaining window)."""
        canonical_name = parked.function
        declared = agent.manifest.function(agent.declared(canonical_name))
        if declared is None:
            return {"error": f"Unknown function '{canonical_name}'"}, "error"
        _, function_spec = declared

        if self.grants is not None and not self.grants.may_reach(canonical_name):
            return {
                "error": (
                    f"'{canonical_name}' is not permitted for this chat — "
                    f"the delegation does not grant it."
                ),
                "not_permitted": True,
            }, "error"

        # Re-judged against THIS connection's grants: an approval given
        # while a person could act on one notebook must not execute after
        # that entitlement was narrowed.
        resumed_scopes = self._scope_values(
            agent, function_spec, parked.inputs
        )
        if self._scope_unjudged(resumed_scopes) or self._scope_unnamed(
                function_spec, resumed_scopes):
            return {"error": (
                f"'{canonical_name}' must be told what it acts on: the "
                f"stored call names no value for it."
            )}, "error"
        if self.grants is not None and not self.grants.allows(
            canonical_name, resumed_scopes
        ):
            return self._out_of_scope(canonical_name, resumed_scopes)

        if action_hash(
            parked.agent_id, parked.function, parked.inputs
        ) != expected_hash:
            return {
                "error": (
                    "Stored action does not match its approval; "
                    "execution blocked."
                ),
            }, "error"

        schema_error = self._validate(
            function_spec.get("inputs"), parked.inputs
        )
        if schema_error:
            return {"error": f"Invalid inputs: {schema_error}"}, "error"

        if decision == "pending":
            approved = False
            if repark is not None:
                try:
                    approved = bool(await repark())
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.logger.error(f"Re-park failed: {exc}")
            decision = "approve" if approved else "deny"

        if decision != "approve":
            return self._denial(
                canonical_name, int(function_spec["permission_level"])
            )

        return await self._execute(
            agent, function_spec, canonical_name, parked.inputs, call_id
        )

    @staticmethod
    def _denial(canonical_name: str, level: int) -> Tuple[Dict[str, Any], str]:
        return {
            "error": (
                f"'{canonical_name}' requires permission level "
                f"{level} and was not approved."
            ),
            "denied": True,
        }, "error"

    # ------------------------------------------------------------------
    async def _resolve_references(self, inputs: Dict[str, Any]):
        """Top-level input values shaped {"storage_ref": …, "path"?: …}
        become the referenced stored value; everything else passes
        through untouched."""
        resolved: Dict[str, Any] = {}
        for name, value in (inputs or {}).items():
            if isinstance(value, dict) and isinstance(
                value.get("storage_ref"), str
            ):
                try:
                    resolved[name] = await self.sinks.read_result(
                        value["storage_ref"], str(value.get("path") or "")
                    )
                except Exception as exc:
                    return None, f"{name}: {exc}"
            else:
                resolved[name] = value
        return resolved, None

    @staticmethod
    def _apply_defaults(
        schema: Dict[str, Any], inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        filled = dict(inputs or {})
        for name, spec in (schema.get("properties") or {}).items():
            if name not in filled and isinstance(spec, dict) and "default" in spec:
                filled[name] = spec["default"]
        return filled

    #: Nothing outside a schema is ever read to check a value against
    #: it: a reference to another document (an address, a file) finds
    #: an empty registry and is an error, never a request.
    _NOWHERE = referencing.Registry()

    @staticmethod
    def _schema_problem(schema: Any) -> str:
        """Why this is not a schema a value can be checked against, or
        ''. Absent is none declared, and not a problem."""
        if not isinstance(schema, dict):
            return ""
        try:
            jsonschema.Draft202012Validator.check_schema(schema)
        except jsonschema.SchemaError as exc:
            return str(exc.message)[:200]
        except Exception as exc:
            return type(exc).__name__
        return ""

    @classmethod
    def _validate(cls, schema: Any, value: Dict[str, Any]) -> Optional[str]:
        if not isinstance(schema, dict):
            return None
        unreadable = cls._schema_problem(schema)
        if unreadable:
            return f"the declared schema is not one ({unreadable})"
        try:
            found = jsonschema.exceptions.best_match(
                jsonschema.Draft202012Validator(
                    schema, registry=cls._NOWHERE).iter_errors(value))
        except Exception as exc:
            # A reference to something that is not in the schema.
            return (f"the declared schema names something outside itself "
                    f"({type(exc).__name__})")
        if found is None:
            return None
        location = ".".join(str(part) for part in found.absolute_path)
        return f"{location or '(root)'}: {found.message}"

    @staticmethod
    def _definitions(agent: InstalledAgent) -> Dict[str, Dict[str, Dict[str, str]]]:
        """kind -> resource id -> {field: storage} from the manifest, so
        ResourceAccess can split fields into keys/values as declared."""
        definitions: Dict[str, Dict[str, Dict[str, str]]] = {}
        for kind in ("secrets", "data"):
            for resource in agent.manifest.resources(kind):
                definitions.setdefault(kind, {})[resource["id"]] = {
                    field["name"]: field.get("storage", "keys")
                    for field in resource.get("fields") or []
                }
        return definitions

    @staticmethod
    def _grants(function_spec: Dict[str, Any]) -> Dict[str, Dict[str, set]]:
        grants: Dict[str, Dict[str, set]] = {}
        for kind, entries in (function_spec.get("resources") or {}).items():
            for resource_id, operations in (entries or {}).items():
                if isinstance(operations, str):
                    operations = [operations]
                grants.setdefault(kind, {})[resource_id] = set(operations)
        return grants

    async def _request_approval(
        self,
        agent: InstalledAgent,
        canonical_name: str,
        level: int,
        chat_level: int,
        inputs: Dict[str, Any],
    ) -> Optional[bool]:
        """Whether the person allowed it — or None when they could not
        be asked at all, which is not their answer."""
        if self.sinks.approve is None:
            return False
        try:
            return bool(await self.sinks.approve({
                "agent_id": agent.agent_id,
                # The id is what the platform routes by; the name is what
                # the person deciding will recognise. An approval is one
                # of the few places that wants both.
                "agent_name": agent.manifest.name,
                "function": canonical_name,
                "permission_level": level,
                "chat_level": chat_level,
                "inputs": inputs,
            }))
        except Exception as exc:
            self.logger.error(f"Approval request failed: {exc}")
            return None

