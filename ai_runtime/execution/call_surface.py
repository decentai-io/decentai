"""What a running call may ask of the chat.

A function's code reaches the chat through its call — ``call.ask``,
``call.post``, ``call.propose``, ``call.credential``, ``call.screen``,
``call.show``, ``call.progress``, ``call.llm``, ``call.install``
(decentai_sdk/base.py). Each lands here first, on the executor's side
of the wire: checked for shape — words, length, how many, what this
call offered and no other — then passed through the chat's door of
the same name (ai_runtime/sinks.py), in the agent's name, bound to
the call it came from.

One surface per executor; ``for_call`` makes the callables one
CallContext carries, bound to that call's id, its resource access and
its code grant. The executor (executor.py) keeps the gates and the
run; nothing here decides whether a call may run, only what it may
ask while it does.
"""

import asyncio
import mimetypes
from typing import Any, Callable, Dict, Optional

from contracts.chat import (
    DISPLAYS_PER_CALL_MAX, POST_MAX_CHARS, POSTS_PER_CALL_MAX, agent_source,
    code_asked, display_stored, event_error, CHOICE_MAX_CHARS, CHOICES_MAX,
    QUESTION_MAX_CHARS, SCREEN_FRAME_MAX_BYTES,
)
from ai_runtime.agents.confinement import Confinement
from ai_runtime.agents.library import InstalledAgent
from ai_runtime.agents.worker_handle import WorkerError
from ai_runtime.execution.code_grant import CodeGrant
from ai_runtime.execution.pictures import Pictures
from ai_runtime.execution.resources import ResourceAccess
from ai_runtime.runtime_logging import RuntimeLoggerFactory
from ai_runtime.sinks import ChatSinks

#: What a frame of a screen carries (contracts/chat.py ScreenFrame).
#: Nothing else an agent sends beside a frame is passed on.
SCREEN_FRAME_FIELDS = ("image_base64", "mime", "width", "height", "frame",
                       "taken", "tabs")


class CallSurface:
    """The chat's doors as a running call may use them, each checked
    here and handed on in the agent's name. Holds the deployment's
    Safety settings too (the sites no agent may open, the packages a
    program may install), because they bound what a call may ask."""

    def __init__(self, sinks: ChatSinks, safety: Optional[Dict[str, Any]] = None,
                 logger=None):
        self.sinks = sinks
        #: What the deployment lets agents do (Settings:Safety), as the
        #: chat's contract carries it. The session writes it again at
        #: each refresh (executor.safety).
        self.safety: Dict[str, Any] = dict(safety or {})
        #: call_id -> whose screen it is, for every call that has shown
        #: a frame and not said it closed. A screen is a call's: when
        #: the call ends, however it ends, whoever is watching is told.
        self._screens: Dict[str, Dict[str, Any]] = {}
        self.logger = logger or RuntimeLoggerFactory.get_logger(
            self.__class__.__name__)

    def for_call(self, agent: InstalledAgent, function_spec: Dict[str, Any],
                 canonical_name: str, call_id: str, access: ResourceAccess,
                 grant: Optional[CodeGrant], displays: list) -> Dict[str, Any]:
        """What one CallContext carries beside its resources, by the
        context's own field names. Declared or nothing: the model only
        where the manifest says ``llm: true``, a login only where it
        says ``credentials: true``, an install only where it said it
        runs code (``grant``)."""
        return {
            "llm": self.llm_for(access) if function_spec.get("llm") is True else None,
            "progress": self.progress_for(agent, canonical_name, call_id),
            "show": self.show_for(canonical_name, displays),
            "post": self.post_for(agent, canonical_name, call_id, displays),
            "ask": self.ask_for(agent, canonical_name, call_id, access),
            "propose": self.propose_for(agent, canonical_name, call_id, grant),
            "install": self.install_for(agent, grant) if grant is not None else None,
            "blocked": self.safety.get("blocked_sites"),
            "credential": (self.credential_for(agent, canonical_name, call_id)
                           if function_spec.get("credentials") is True else None),
            "screen": self.screen_for(agent, canonical_name, call_id),
        }

    def llm_for(self, access: ResourceAccess) -> Optional[Callable]:
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

    def ask_for(self, agent: InstalledAgent, canonical_name: str,
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

    def propose_for(self, agent: InstalledAgent, canonical_name: str,
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
                why = CodeGrant.problem(asked, self.listed_packages())
                if why:
                    raise WorkerError(f"The code could not be proposed: {why}")
            allowed = await self.sinks.propose(asked, source)
            if allowed is True and grant is not None:
                grant.allow(asked)
            return allowed
        return propose

    def listed_packages(self) -> Optional[list]:
        """The packages a program may install here, or None where the
        deployment keeps no list."""
        if self.safety.get("packages") != "listed":
            return None
        return [str(name) for name in self.safety.get("allowed_packages") or []]

    def install_for(self, agent: InstalledAgent,
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

    def credential_for(self, agent: InstalledAgent, canonical_name: str,
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

    def screen_for(self, agent: InstalledAgent, canonical_name: str,
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

    async def end_screen(self, call_id: str) -> None:
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

    def post_for(self, agent: InstalledAgent, canonical_name: str,
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

    def show_for(self, canonical_name: str,
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

    def progress_for(self, agent: InstalledAgent, canonical_name: str,
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
