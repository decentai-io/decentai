"""One running call: what it may ask of the chat, and what it holds.

A function's code reaches the chat through its call — ``call.ask``,
``call.post``, ``call.propose``, ``call.credential``, ``call.screen``,
``call.show``, ``call.progress``, ``call.llm``, ``call.install``,
``call.resources.*`` (decentai_sdk/base.py). Each line the worker sends
lands here, on a method of the same name: checked for shape — words,
length, how many, what this call offered and no other, what its
manifest declared — then passed through the chat's door of the same
name (ai_runtime/execution/sinks.py), in the agent's name, bound to this call.

The executor (executor.py) builds one Call per invocation, past the
gates, and hands it to the pool (agents/pool.py), which binds it to
the worker the invocation goes to and answers that worker's asks from
it (``answer``) and no other's. The Call is the invocation's whole
authority — the mediated resources, the model only where the manifest
said ``llm: true``, a login only where it said ``credentials: true``,
an install only where it said ``code: true`` — and the worker holds
none of it.

It holds what lives exactly as long as the call, too: the displays it
offered, how often it posted, the screen it is showing, the hosts a
credential it was handed named, and — for a function that runs code —
what the person allowed on its cards: the hosts they named are open on
the worker's way out until the call ends (egress.py), and the packages
they named may be installed (environments.py ``extras``). Those names
are checked before a card is shown, so that what a person reads is
what would be opened: a host is a name, never an address and never
every host under a name; a package is a name and a version. The
function's clock stops while a person decides (``paused``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
import re
from typing import Any, Dict, List, Optional

from contracts.agent_manifest import ADDRESS_PATTERN, HOST_PATTERN
from contracts.chat import (
    DISPLAYS_PER_CALL_MAX, POST_MAX_CHARS, POSTS_PER_CALL_MAX, agent_source,
    code_asked, display_stored, event_error, CHOICE_MAX_CHARS, CHOICES_MAX,
    QUESTION_MAX_CHARS, SCREEN_FRAME_MAX_BYTES,
)
from ai_runtime.agents.environments import AgentEnvironment
from ai_runtime.agents.workers import WorkerError
from ai_runtime.execution.pictures import Pictures
from ai_runtime.execution.sinks import ChatSinks

#: The one refusal call.llm() can meet — same wording as the SDK's own,
#: so the function's author reads one message wherever it runs.
LLM_UNAVAILABLE = (
    "The platform model is not available here — the function must "
    "declare `llm: true` in its manifest, and the invocation must run "
    "inside a chat with a model."
)

#: What a frame of a screen carries (contracts/chat.py ScreenFrame).
#: Nothing else an agent sends beside a frame is passed on.
SCREEN_FRAME_FIELDS = ("image_base64", "mime", "width", "height", "frame",
                       "taken", "tabs")


class Call:
    """One invocation, from past the gates to its line on the trail."""

    #: Wire method -> (the method here, its parameters in the wire's
    #: names, the key its answer rides under). ``show`` takes the whole
    #: request as its spec; ``llm`` answers a shape of its own.
    ASKS = {
        "llm.complete": ("llm", ("messages", "max_tokens", "images"), None),
        "ask": ("ask", ("question", "choices", "expects"), "answer"),
        "propose": ("propose", ("code",), "allowed"),
        "install": ("install", ("packages",), "folder"),
        "credential": ("credential",
                       ("host", "fields", "account", "site", "refresh"), "values"),
        "post": ("post", ("text", "displays"), "posted"),
        "show": ("show", None, "display_id"),
    }

    #: ``resources.<operation>`` -> its parameters, in call order.
    RESOURCES = {
        "use_secret": ("resource_id", "ref"),
        "list_secrets": ("resource_id",),
        "list_data": ("resource_id", "filters"),
        "read_data": ("resource_id", "ref"),
        "create_data": ("resource_id", "fields"),
        "update_data": ("resource_id", "ref", "fields"),
        "delete_data": ("resource_id", "ref"),
        "list_files": ("resource_id",),
        "read_file": ("resource_id", "ref"),
        "create_file": ("resource_id", "filename", "content", "content_base64"),
        "delete_file": ("resource_id", "ref"),
    }

    #: What one login ask may carry (contracts/chat.py CredentialAsk
    #: keeps the same bounds on the card).
    CREDENTIAL_FIELDS_MAX = 20

    def __init__(self, call_id: str, agent: Any = None,
                 function_spec: Optional[Dict[str, Any]] = None,
                 canonical_name: str = "", job_id: str = "",
                 resources: Any = None, sinks: Optional[ChatSinks] = None,
                 safety: Optional[Dict[str, Any]] = None,
                 confinement: Any = None, logger=None) -> None:
        self.call_id = str(call_id)
        self.agent = agent
        #: The function as its manifest declares it: what the call may
        #: be given hangs on ``llm``, ``credentials`` and ``code``.
        self.spec: Dict[str, Any] = dict(function_spec or {})
        self.name = str(canonical_name or "")
        #: The job the call runs for, named in everything it says to
        #: the chat: a card it opens parks that job, not the mind.
        self.job_id = str(job_id or "")
        #: The mediated resources (resources.py): only the ids and the
        #: operations the function declared answer.
        self.resources = resources
        #: The chat's doors (sinks.py). A door that is None has nobody
        #: behind it, and each ask says here what that means for it.
        self.sinks = sinks or ChatSinks()
        #: What the deployment lets agents do (Settings:Safety): the
        #: sites no agent may open, the packages a program may install.
        self.safety: Dict[str, Any] = dict(safety or {})
        #: Where packages a card allowed are built: the builder's place
        #: (confinement.py).
        self.confinement = confinement
        self.logger = logger or logging.getLogger(self.__class__.__name__)
        #: Who is speaking, in everything the call says to the chat.
        self.source = agent_source(
            str(getattr(agent, "agent_id", "") or ""),
            str(getattr(getattr(agent, "manifest", None), "name", "") or ""),
            self.name, call_id=self.call_id, job_id=self.job_id)
        #: Sites no agent may open here, told to the worker's proxy
        #: pass as the call begins.
        self.blocked: List[str] = [
            str(name) for name in self.safety.get("blocked_sites") or []]
        #: What the call offered to show (``show``), riding its result.
        self.displays: list = []
        self._posted = 0
        self._storing = 0
        #: Whether a screen it showed is still open.
        self.showing = False
        #: host -> connections the call's worker made while it ran,
        #: filled in when it ends, for the trail.
        self.reached: Dict[str, int] = {}
        #: Hosts a credential handed to this call named (``from_secret``),
        #: open on the worker's way out until the call ends.
        self.learned: list = []
        #: Packages the person allowed on a card of this call, as the
        #: card wrote them; and the hosts those cards lent.
        self.packages: set = set()
        self._lent: list = []
        #: The function's clock stops while a person decides: how many
        #: asks are open, and how long answered ones waited (pool.py).
        self.waiting = 0
        self.waited = 0.0
        #: The worker the invocation was sent to, bound by the pool
        #: before the invoke goes out. An ask is answered only from it.
        self.handle: Any = None

    @property
    def runs_code(self) -> bool:
        return self.spec.get("code") is True

    # ------------------------------------------------------------------
    # The wire
    # ------------------------------------------------------------------

    async def answer(self, method: str, params: Dict[str, Any]) -> Any:
        """One ask from the worker, answered as the wire shapes it
        (docs/reference/worker-protocol.md). An unknown method is
        refused, as is anything a method refuses."""
        kind, _, operation = method.partition(".")
        if kind == "resources":
            return await self.resource(operation, params)
        asked = self.ASKS.get(method)
        if asked is None:
            raise WorkerError(f"unknown ask '{method}'")
        name, parameters, key = asked
        if parameters is None:
            given: tuple = ({k: v for k, v in params.items() if k != "call_id"},)
        else:
            given = tuple(params.get(p) for p in parameters)
        answered = await getattr(self, name)(*given)
        return answered if key is None else {key: answered}

    async def resource(self, operation: str, params: Dict[str, Any]) -> Any:
        """``call.resources.<operation>``: the mediated resources, and
        for a credential the host it names, lent to the worker until
        the call ends."""
        names = self.RESOURCES.get(operation)
        if names is None or self.resources is None:
            raise WorkerError(f"unknown ask 'resources.{operation}'")
        answer = await getattr(self.resources, operation)(
            *(params.get(name) for name in names))
        if operation == "use_secret" and self.handle is not None:
            # A credential may name the host it is for — a person's own
            # site — and the manifest said which field does. Handed
            # over, it is a host the worker may reach until this call
            # ends.
            self.learned.extend(self.handle.place.learn(
                str(params.get("resource_id") or ""), answer) or [])
        if operation in ("delete_data", "delete_file"):
            return {"deleted": bool(answer)}
        return answer

    @contextlib.asynccontextmanager
    async def paused(self):
        """A person is deciding: the function's clock stops meanwhile."""
        loop = asyncio.get_running_loop()
        started = loop.time()
        self.waiting += 1
        try:
            yield
        finally:
            self.waiting -= 1
            self.waited += loop.time() - started

    # ------------------------------------------------------------------
    # The model
    # ------------------------------------------------------------------

    async def llm(self, messages: Any, max_tokens: Any = None,
                  images: Any = None) -> Dict[str, str]:
        """The chat's model, for a function whose manifest says ``llm:
        true``, with pictures resolved here. An agent names a picture
        (``{resource_id, ref}``); the bytes are read under this call's
        own file grant — a function shows the model only what its
        manifest lets it read — and handed on as ``{mime,
        content_base64}``. A picture the agent carries itself is taken
        too, and either way it is checked to be one (pictures.py)."""
        if self.spec.get("llm") is not True or self.sinks.llm is None:
            raise WorkerError(LLM_UNAVAILABLE)
        images = [i for i in images or [] if isinstance(i, dict)]
        if len(images) > Pictures.MAX_PER_ASK:
            raise WorkerError(f"The model can be shown at most "
                              f"{Pictures.MAX_PER_ASK} pictures at once.")
        pictures = []
        for named in images:
            if named.get("content_base64"):
                # Carried, not named: the agent's word for what it is
                # counts for nothing until the bytes agree.
                pictures.append(self._picture(
                    "it", named.get("mime"), named.get("content_base64")))
                continue
            record = await self.resources.read_file(
                str(named.get("resource_id") or ""), str(named.get("ref") or ""))
            mime = str(record.get("file_type") or "") or (
                mimetypes.guess_type(str(record.get("filename") or ""))[0] or "")
            pictures.append(self._picture(
                f"'{named.get('ref')}'", mime, str(record.get("content_base64") or "")))
        if pictures:
            text = await self.sinks.llm(messages or [], max_tokens, pictures)
        else:
            text = await self.sinks.llm(messages or [], max_tokens)
        return {"text": str(text),
                "stop_reason": str(getattr(text, "stop_reason", "") or "")}

    @staticmethod
    def _picture(name: str, mime: Any, encoded: Any) -> Dict[str, str]:
        try:
            return Pictures.checked(mime, encoded)
        except ValueError as why:
            raise WorkerError(
                f"{name} is not an image the model can be shown: {why}.")

    # ------------------------------------------------------------------
    # The person
    # ------------------------------------------------------------------

    async def ask(self, question: Any, choices: Any,
                  expects: Any = "") -> Optional[Any]:
        """The agent asking the person: checked here (words, length,
        choices), then a card in the agent's name. None where there is
        no one to ask. ``expects="file"`` asks for a document, and the
        file the person gives is handed to this call."""
        if self.sinks.ask is None:
            return None
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
        async with self.paused():
            if expects == "file":
                # Buttons answer with words; a file request has none.
                answer = await self.sinks.ask(question, [], self.source,
                                              expects="file")
                if self.resources is not None:
                    self.resources.hand(answer)
                return answer
            return await self.sinks.ask(question, offered, self.source)

    async def propose(self, code: Any) -> Optional[bool]:
        """Code the agent wants to run, checked here against the card's
        own shape (contracts/chat.py CodeAsk), then put before the
        person. None where there is no one to ask. For a function that
        declared ``code: true`` an allowed card is a grant for this call:
        its hosts are opened and its packages may be installed, so the
        names are checked before the card is shown."""
        if self.sinks.propose is None:
            return None
        if not isinstance(code, dict):
            raise WorkerError("A proposal is code, its purpose and what it needs.")
        # A review is the platform's to write, never the agent's.
        asked, why = code_asked(
            {key: value for key, value in code.items() if key != "review"})
        if asked is None:
            raise WorkerError(f"The code could not be proposed: {why}")
        if self.runs_code:
            why = self.code_problem(asked, self.listed_packages())
            if why:
                raise WorkerError(f"The code could not be proposed: {why}")
        async with self.paused():
            allowed = await self.sinks.propose(asked, self.source)
        if allowed is True and self.runs_code:
            self.allow(asked)
        return allowed

    async def install(self, packages: Any) -> str:
        """Packages for code the person allowed: exactly the ones a card
        of this call named, installed beside the agent's own environment
        by the platform — downloaded and built where that is confined,
        and never by the code that asked. Not counted against the
        function's own clock."""
        if not self.runs_code:
            raise WorkerError(
                "Packages are installed only for a function that declares "
                "`code: true` in its manifest.")
        names = [str(p).strip() for p in packages or [] if str(p).strip()] \
            if isinstance(packages, list) else []
        if not names:
            raise WorkerError("Name the packages to install.")
        refused = self.refused(names)
        if refused is not None:
            raise WorkerError(
                f"'{refused}' is not on a card the person allowed: put the "
                f"code that needs it before them first.")
        extras = getattr(getattr(self.agent, "environment", None), "extras", None)
        if extras is None or self.confinement is None:
            raise WorkerError("Packages cannot be installed here.")
        async with self.paused():
            folder, errors = await extras(names, self.confinement.builder_place())
        if errors:
            raise WorkerError("; ".join(errors))
        return str(folder)

    async def credential(self, host: Any, fields: Any, account: Any = None,
                         site: Any = None, refresh: Any = False) -> Optional[Any]:
        """A login the agent asks for as it works, for a function whose
        manifest says ``credentials: true``: checked here for shape,
        then resolved through the person's cards in the agent's name."""
        if self.spec.get("credentials") is not True or self.sinks.credential is None:
            raise WorkerError(
                "Logins are not available here — the function must declare "
                "`credentials: true` in its manifest, and the invocation "
                "must run inside a chat.")
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
        async with self.paused():
            return await self.sinks.credential(
                host, cleaned, str(account or "")[:200] or None,
                str(site or "")[:200] or None, bool(refresh), self.source)

    # ------------------------------------------------------------------
    # The chat
    # ------------------------------------------------------------------

    async def post(self, text: Any, display_ids: Any) -> bool:
        """The agent speaking for itself: checked here (words, length,
        how many, displays this call offered and no other), then a
        message in the agent's name. False where there is no chat."""
        if self.sinks.post is None:
            return False
        text = str(text or "").strip()
        if not text:
            raise WorkerError("A post needs words.")
        if len(text) > POST_MAX_CHARS:
            raise WorkerError(f"A post may be at most {POST_MAX_CHARS} characters.")
        if self._posted >= POSTS_PER_CALL_MAX:
            raise WorkerError(f"A call may post at most {POSTS_PER_CALL_MAX} times.")
        offered = {d["display_id"]: d for d in self.displays}
        parts = []
        for display_id in [str(d) for d in display_ids or []]:
            display = offered.get(display_id)
            if display is None:
                raise WorkerError(f"'{display_id}' is not a display this call offered.")
            chart = display.get("kind") == "chart"
            parts.append({
                "type": "graph" if chart else "table",
                "text": display.get("title") or ("Chart" if chart else "Table"),
                "storage_ref": display_id, "source": self.source,
            })
        self._posted += 1
        return bool(await self.sinks.post(text, self.source, parts))

    async def show(self, spec: Dict[str, Any]) -> Optional[str]:
        """What the call offers to show, checked and kept: its shape and
        size against the chat contract, its rows in chat storage — the
        display's id is its storage ref. Nothing here reaches a person:
        the assistant's say.show does, and only for a successful call.
        None where nobody could see it (no storage: the clock, tests)."""
        if self.sinks.store is None:
            return None
        # Displays on their way to the store count with those kept, so
        # a dozen offered at once are held to the same limit.
        if len(self.displays) + self._storing >= DISPLAYS_PER_CALL_MAX:
            raise WorkerError(f"A call may offer at most "
                              f"{DISPLAYS_PER_CALL_MAX} displays.")
        stored, problem = display_stored(spec)
        if problem:
            raise WorkerError(f"Display refused: {problem}")
        self._storing += 1
        try:
            storage_ref = await self.sinks.store(self.name, stored)
        except Exception as exc:
            self.logger.warning(f"Display storage failed: {exc}")
            storage_ref = None
        finally:
            self._storing -= 1
        if not storage_ref:
            # Nowhere to keep it, or the store failed and said so: the
            # offer is simply not there to show, and the function runs on.
            return None
        self.displays.append({
            "display_id": storage_ref, "kind": str(spec.get("kind")),
            "title": str(spec.get("title") or ""),
        })
        return storage_ref

    async def progress(self, description: Any) -> None:
        """The agent's own narration, told with who is speaking. Nothing
        where nobody listens (the clock, tests)."""
        text = str(description or "").strip()
        if text and self.sinks.progress is not None:
            await self.sinks.progress(text, self.source)

    async def screen(self, kind: str, params: Dict[str, Any]) -> None:
        """A screen the function shows, in the agent's name, to whoever
        is watching the chat: a frame, or its closing."""
        if self.sinks.screen is None:
            return
        if kind != "frame":
            self.showing = False
            await self.sinks.screen(kind, {"call_id": self.call_id}, self.source)
            return
        # What reaches a person's browser is a frame by the chat's
        # contract and a picture by its own bytes; anything else an
        # agent sent as one is dropped, and the function runs on.
        frame = {name: params[name] for name in SCREEN_FRAME_FIELDS if name in params}
        frame["call_id"] = self.call_id
        if event_error({"event": "screen_frame", **frame}) or Pictures.problem(
                frame.get("mime", "image/jpeg"), frame.get("image_base64"),
                Pictures.FRAME_TYPES):
            return
        # A frame is small because many are sent, to every browser that
        # watches. The SDK keeps to the size; an agent that does not use
        # it is held to it here.
        if len(frame["image_base64"]) > SCREEN_FRAME_MAX_BYTES * 4 // 3 + 4:
            return
        self.showing = True
        await self.sinks.screen(kind, frame, self.source)

    async def end_screen(self) -> None:
        """The call is over: a screen it showed and never closed is
        closed for it. A function that returned, raised, ran out of
        time or lost its worker says nothing of its screen, and a
        picture left standing reads as something still running."""
        if not self.showing or self.sinks.screen is None:
            return
        self.showing = False
        try:
            await self.sinks.screen("closed", {"call_id": self.call_id}, self.source)
        except Exception as exc:
            self.logger.warning(f"A screen could not be told closed: {exc}")

    # ------------------------------------------------------------------
    # What a card allowed
    # ------------------------------------------------------------------

    def listed_packages(self) -> Optional[list]:
        """The packages a program may install here, or None where the
        deployment keeps no list."""
        if self.safety.get("packages") != "listed":
            return None
        return [str(name) for name in self.safety.get("allowed_packages") or []]

    @classmethod
    def code_problem(cls, code: Dict[str, Any],
                     listed: Optional[List[str]] = None) -> str:
        """Why a proposal cannot be granted as written, or ''.

        ``listed`` is the deployment's list of packages a program may
        install (Settings:Safety), or None where any may be named."""
        for host in code.get("hosts") or []:
            if not cls.host(host):
                return (f"'{host}' is not a host a person can allow: name "
                        f"one host, such as api.example.com, or "
                        f"api.example.com:8443")
        for package in code.get("packages") or []:
            # Read once, the way it will be installed: the name checked
            # against the list is the name handed to the installer, and
            # not another reading of the same words.
            asked = AgentEnvironment.requirement(package)
            if not asked:
                return (f"'{package}' is not a package a person can allow: "
                        f"name it, and its version where that matters — "
                        f"pandas, requests==2.32.3")
            if listed is not None and cls.package(asked) not in {
                    cls.package(name) for name in listed}:
                return (f"'{package}' is not on the list of packages a "
                        f"program may install here. The list is: "
                        f"{', '.join(sorted(listed)) or 'empty'}")
        return ""

    @staticmethod
    def package(requirement: str) -> str:
        """The name in a requirement, as an index compares names: no
        version, no extras, lower case, and ``_`` and ``.`` as ``-``."""
        name = re.split(r"[\[=<>!~\s]", str(requirement or "").strip(), 1)[0]
        return re.sub(r"[-_.]+", "-", name).lower()

    @staticmethod
    def host(written: str) -> str:
        """A host as a card may name it — ``name`` or ``name:port`` —
        or '' when it is not one."""
        written = str(written or "").strip().lower()
        name, colon, port = written.rpartition(":")
        if not colon:
            name, port = written, ""
        elif not (port.isdigit() and 1 <= int(port) <= 65535):
            return ""
        if (name.startswith("*.") or ADDRESS_PATTERN.match(name)
                or not HOST_PATTERN.match(name)):
            return ""
        return written

    def allow(self, code: Dict[str, Any]) -> None:
        """A card the person allowed: its hosts are opened on the
        worker's way out, and its packages may be installed."""
        self.packages.update(str(p) for p in code.get("packages") or [])
        hosts = [self.host(h) for h in code.get("hosts") or []]
        place = getattr(self.handle, "place", None)
        if place is not None and hosts:
            self._lent.extend(place.lend(hosts))

    def refused(self, packages: List[str]) -> Optional[str]:
        """The first of these no card of this call allowed, or None."""
        return next((p for p in packages if p not in self.packages), None)

    def close(self) -> None:
        """The call ended: what was opened for it — the hosts its cards
        lent, the hosts its credentials named — is closed. The worker
        goes on to serve somebody else's call."""
        place = getattr(self.handle, "place", None)
        if place is not None:
            place.take_back(self._lent + self.learned)
        self._lent, self.learned = [], []
