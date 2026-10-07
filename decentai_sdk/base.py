"""The classes agent code subclasses (docs/agents/sdk.md).

Code mirrors the manifest: an AgentBase subclass composes ToolBase
subclasses; each manifest function is a method on its tool class, named by
the function id. The manifest is injected — code declares no metadata, so
contract and implementation cannot drift.

Only manifest-declared functions resolve. Every function receives one
argument, the FunctionCall: validated inputs, and the platform's services
for that one call — its records, files and secrets (exactly the
operations the function declared, and the values of the secrets granted
to its slot), the chat's model, the person. Functions return
``(result, status)`` with status ``success`` or ``error``.

Stdlib only, on purpose: this file runs inside a worker's private venv,
where the platform's own packages do not exist.
"""

from __future__ import annotations

import asyncio
import base64
import keyword
import logging
from typing import Any, Callable, Dict, List, Optional

from decentai_sdk.manifest import Manifest


class Completion(str):
    """What ``call.llm()`` answers: the model's words, as text, that
    also say whether the provider stopped the model before it was
    done. ``stop_reason`` is the provider's own word ("stop", "length",
    "max_tokens", ...) or empty; ``cut`` is true when the reply was cut
    off by a token cap, so a function can ask again for less rather
    than parse half an answer."""

    CUT = ("length", "max_tokens")

    def __new__(cls, text: Any = "", stop_reason: Any = ""):
        completion = super().__new__(cls, str(text or ""))
        completion.stop_reason = str(stop_reason or "")
        return completion

    @property
    def cut(self) -> bool:
        return self.stop_reason in self.CUT


class ResourceDenied(Exception):
    """The platform refused a resource ask — an undeclared operation, a
    missing binding, or a capability this invocation does not carry.
    Part of the SDK because agent code catches it."""


class Show:
    """What a function may offer to show: a table or a chart, built
    from its own data.

    An offer, not a message. The platform checks it and keeps it with
    the call's result; the assistant decides whether the person sees it,
    and the page draws it with the agent's name beside it. Each method
    returns the display's id — or None where nobody could see it (a
    test with no chat), so the same code
    runs everywhere. A display the platform refuses (too large, a chart
    whose series do not match its labels) raises ResourceDenied saying
    why.
    """

    def __init__(self, sink: Optional[Callable] = None):
        self._sink = sink

    async def table(self, rows: List[Dict[str, Any]],
                    columns: Optional[List[str]] = None,
                    title: str = "") -> Optional[str]:
        """Rows of plain values; ``columns`` picks and orders them."""
        return await self._offer({
            "kind": "table", "rows": list(rows or []),
            "columns": list(columns) if columns else None,
            "title": str(title or ""),
        })

    async def chart(self, chart_type: str, labels: List[Any],
                    series: List[Dict[str, Any]],
                    title: str = "") -> Optional[str]:
        """``chart_type`` is bar, line or pie; ``series`` is a list of
        ``{"name": …, "values": […]}`` with one value per label."""
        return await self._offer({
            "kind": "chart", "chart_type": str(chart_type),
            "labels": [str(label) for label in labels or []],
            "series": list(series or []), "title": str(title or ""),
        })

    async def _offer(self, spec: Dict[str, Any]) -> Optional[str]:
        if self._sink is None:
            return None
        return await self._sink(spec)


class Screen:
    """A screen the person can watch in the chat, and take over when
    asked (``call.screen``): the browser a function drives, as pictures.

    ``show`` pushes one frame; ``inputs`` drains what the person did
    since last asked — mouse, keyboard and wheel events, and the moment
    they took or released control; ``taken`` says whether they hold it
    now; ``said`` drains what they wrote in the chat while this call
    runs, so a running function can be steered in words. Frames go to
    the chat's audience and nowhere else; nothing is recorded. A
    function that shows nothing pays nothing."""

    #: What one frame may weigh, base64 excluded (contracts/chat.py).
    MAX_BYTES = 300_000
    #: How many tabs a frame may tell of, and how long a name or an
    #: address may be (contracts/chat.py).
    TABS_MAX = 20
    #: the largest place a tab may say it has (contracts/chat.py, ScreenTab)
    TAB_INDEX_MAX = 1000
    TAB_TITLE_CHARS = 200
    TAB_ADDRESS_CHARS = 300

    def __init__(self, send: Optional[Callable] = None):
        self._send = send
        self._seq = 0
        self._pending: List[Dict[str, Any]] = []
        self._said: List[str] = []
        self._arrived: Optional[Any] = None
        self.taken = False
        self.open = False
        #: the person closed the live view: a function that only shows
        #: (a watch) ends; one that works on regardless ignores it
        self.closed = False

    async def show(self, image: bytes, width: int, height: int,
                   mime: str = "image/jpeg",
                   tabs: Optional[List[Dict[str, Any]]] = None) -> bool:
        """One frame. False where nobody could see it (a test)
        or the frame is too large; nothing raises.

        ``tabs`` tells what else is open behind the picture — the tabs
        of a browser, the windows of an application — each as
        ``{"index", "title", "address", "active"}``, in their order,
        ``index`` from 1. The person sees them above the picture and,
        holding control, goes to one, closes one or opens one: that
        arrives among ``inputs`` as ``{"type": "tab", "action":
        "switch" | "close" | "new", "index"}``."""
        if self._send is None or not image or len(image) > self.MAX_BYTES:
            return False
        self._seq += 1
        self.open = True
        frame = {
            "image_base64": base64.b64encode(bytes(image)).decode("ascii"),
            "mime": str(mime or "image/jpeg"), "width": int(width),
            "height": int(height), "frame": self._seq, "taken": self.taken,
        }
        told = self._tabs(tabs)
        if told:
            frame["tabs"] = told
        await self._send("screen.frame", frame)
        return True

    @classmethod
    def _tabs(cls, tabs: Any) -> List[Dict[str, Any]]:
        """The tabs as the frame carries them: what does not read as a
        tab is left out rather than refused, since a frame is the
        present tense and the next one is a moment away."""
        told = []
        for position, tab in enumerate(tabs if isinstance(tabs, list) else [], 1):
            if not isinstance(tab, dict) or len(told) >= cls.TABS_MAX:
                continue
            try:
                index = int(tab.get("index") or position)
            except (TypeError, ValueError):
                index = position
            told.append({
                "index": min(max(1, index), cls.TAB_INDEX_MAX),
                "title": str(tab.get("title") or "")[: cls.TAB_TITLE_CHARS],
                "address": str(tab.get("address") or "")[: cls.TAB_ADDRESS_CHARS],
                "active": tab.get("active") is True,
            })
        return told

    async def close(self) -> None:
        if self._send is None or not self.open:
            return
        self.open = False
        await self._send("screen.closed", {})

    def inputs(self) -> List[Dict[str, Any]]:
        """What the person did since last asked, oldest first, and
        nothing twice."""
        events, self._pending = self._pending, []
        return events

    async def wait_input(self, timeout: float = 1.0) -> List[Dict[str, Any]]:
        """The next inputs, waiting up to ``timeout`` seconds for any."""
        if not self._pending:
            self._arrived = asyncio.get_running_loop().create_future()
            try:
                await asyncio.wait_for(self._arrived, timeout)
            except asyncio.TimeoutError:
                pass
            finally:
                self._arrived = None
        return self.inputs()

    def said(self) -> List[str]:
        """What the person wrote in the chat since last asked, oldest
        first — words for the function, not for the page, so they are
        never among ``inputs``."""
        said, self._said = self._said, []
        return said

    def receive(self, events: List[Dict[str, Any]]) -> None:
        """The wire delivering the person's actions (worker.py)."""
        woke = False
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("type") == "say":
                text = str(event.get("text") or "").strip()
                if text:
                    self._said.append(text)
                continue
            if event.get("type") == "control":
                self.taken = event.get("action") == "take"
                if event.get("action") == "close":
                    self.closed = True
            self._pending.append(dict(event))
            woke = True
        if woke and self._arrived is not None and not self._arrived.done():
            self._arrived.set_result(True)


class FunctionCall:
    """Everything one function invocation receives.

    Built by the platform: ``inputs`` are already validated against the
    manifest schema; ``resources`` is scoped to the function's declared
    resources and operations; ``progress`` reaches the person's chat as
    a transient line; ``llm`` asks the chat's model; ``show`` offers a
    table or a chart; ``post`` says something to the person directly;
    ``ask`` asks them; ``credential`` asks them for a site's sign-in;
    ``propose`` puts code before them before it runs, and ``install``
    installs the packages they allowed; ``screen`` shows what the
    function drives; ``conversation`` names the chat it runs in.
    """

    def __init__(self, inputs: Dict[str, Any], progress_sink: Optional[Callable] = None,
                 resources: Any = None, llm: Optional[Callable] = None,
                 show: Optional[Callable] = None,
                 post: Optional[Callable] = None,
                 ask: Optional[Callable] = None,
                 credential: Optional[Callable] = None,
                 screen: Optional["Screen"] = None,
                 conversation: str = "",
                 propose: Optional[Callable] = None,
                 install: Optional[Callable] = None):
        self.inputs = dict(inputs or {})
        #: The conversation this call runs in — an opaque key, the
        #: same for every call of one chat and different for every
        #: other, so a function may keep something (a browser) open
        #: between calls for the person who is talking to it, and
        #: never hand it to another. Empty where there is no chat.
        self.conversation = str(conversation or "")
        self.resources = resources
        self.show = Show(show)
        #: A screen the person can watch and take over — the browser
        #: this function drives, as pictures. One that shows nothing
        #: where there is nobody to see it (a test).
        self.screen = screen if screen is not None else Screen()
        self._post = post
        self._ask = ask
        self._propose = propose
        self._install = install
        #: A login the function needs for a host it is working on,
        #: asked for as it works (call.credential). Present only when
        #: the manifest declares ``credentials: true`` on this function.
        self._credential = credential
        self._progress_sink = progress_sink
        # The chat's model as a call the platform makes: the agent gets
        # the completion, never the model's key. Present only when the
        # manifest declares
        # ``llm: true`` on this function AND the invocation runs where a
        # chat model exists.
        self._llm = llm

    async def progress(self, description: str) -> None:
        if self._progress_sink is not None:
            await self._progress_sink(str(description))

    async def post(self, text: str, show: Optional[List[str]] = None) -> bool:
        """Say something to the person yourself, now: a message in the
        chat under the assistant, with this agent's name beside it.
        ``show`` names displays this call offered (``call.show``) to ride
        along. The assistant hears it too, as the agent's words.

        True when it reached a chat, False where there is none (a
        test); a post the platform refuses — empty, too long,
        too many, a display this call did not offer — raises
        ResourceDenied saying why."""
        if self._post is None:
            return False
        return bool(await self._post(str(text or ""),
                                     [str(d) for d in show or []]))

    async def ask(self, question: str,
                  choices: Optional[List[str]] = None,
                  expects: Optional[str] = None) -> Optional[str]:
        """Ask the person, and wait for the answer. ``choices`` are
        offered as buttons; the person may always answer in their own
        words. The function's clock stops while it waits.

        ``expects="file"`` asks for a document instead of words: the
        person attaches one, and the answer is its file ref — readable
        with ``call.resources.read_file`` through a files resource this
        function declares. Anything else expects text.

        The answer's text — or None when nobody answered: no chat to ask
        in (a test), or a day went by. A question the platform
        refuses (empty, too long, too many choices) raises
        ResourceDenied saying why."""
        if self._ask is None:
            return None
        expects = str(expects or "").strip().lower()
        if expects and expects not in ("text", "file"):
            raise ResourceDenied("expects must be 'text' or 'file'")
        answer = await self._ask(str(question or ""),
                                 [str(c) for c in choices or []],
                                 **({"expects": expects} if expects else {}))
        return None if answer is None else str(answer)

    async def propose(self, code: str, purpose: str,
                      language: str = "python", where: str = "",
                      packages: Optional[List[str]] = None,
                      hosts: Optional[List[str]] = None,
                      credentials: Optional[List[str]] = None,
                      files: Optional[List[str]] = None) -> Optional[bool]:
        """Put code before the person before running it, and wait for
        their answer. The function's clock stops while it waits.

        ``code`` is shown whole, and ``purpose`` says what it is for in
        words a person who does not read code can weigh. ``language``
        is ``python`` or ``javascript``. ``where`` names where it runs
        when that is not this agent's own sandbox — the site, for a
        script in a page. What the code needs is named too: ``packages``
        to install, ``hosts`` to reach, ``credentials`` to be handed and
        ``files`` to read, each as the person would know it.

        The assistant reads the code first and its note rides on the
        card: whether the code does what the purpose says.

        True when the person allowed it, False when they did not — and
        None when nobody answered: no chat to ask in (a test),
        or a day went by. Run the code only on True. A proposal
        the platform refuses (no code, no purpose, too long, a language
        it does not know) raises ResourceDenied saying why."""
        if self._propose is None:
            return None
        answer = await self._propose({
            "code": str(code or ""), "purpose": str(purpose or ""),
            "language": str(language or "").strip().lower(),
            "where": str(where or ""),
            "packages": [str(p) for p in packages or []],
            "hosts": [str(h) for h in hosts or []],
            "credentials": [str(c) for c in credentials or []],
            "files": [str(f) for f in files or []],
        })
        return None if answer is None else bool(answer)

    async def install(self, packages: List[str]) -> str:
        """Packages for code the person allowed, installed by the
        platform: the folder they are in, to put on the path of the
        program that runs the code (``PYTHONPATH``).

        Only what a card of this call named, written as the card wrote
        it, is installed: propose the code first, and install what it
        needs once the person has allowed it. A list installed before
        is handed back at once. The function's clock stops meanwhile.

        A function whose manifest does not declare ``code: true``, a
        package no allowed card named, and an installation that fails
        all raise ResourceDenied saying why."""
        if self._install is None:
            raise ResourceDenied(
                "Packages are installed only for a function that declares "
                "`code: true` in its manifest.")
        return str(await self._install([str(p) for p in packages or []]))

    async def credential(self, host: str, fields: List[Dict[str, Any]],
                         account: Optional[str] = None,
                         site: Optional[str] = None,
                         refresh: bool = False) -> Optional[Dict[str, Any]]:
        """A login for a host, asked for as the function works.

        ``host`` is the page holding the form — its domain keys the
        saved login, so ``id.atlassian.com`` and ``atlassian.net`` sites
        share one. ``fields`` say what the form asks for::

            [{"name": "email", "label": "Email", "type": "text"},
             {"name": "password", "label": "Password", "type": "secret"},
             {"name": "otp", "label": "One-time code", "type": "secret",
              "remember": False}]

        ``site`` names where it is being used (the page's host and path)
        for the person's consent, which is per agent and per site.
        ``account`` picks one of several saved logins by address.
        ``refresh`` asks for everything again, for a changed password.

        The person sees a card: to type the login the first time, to
        allow this agent on this site, to choose between accounts, or to
        type a field that is asked every time. The function's clock
        stops meanwhile. The answer is the fields as a map, with the
        host and the account beside them — or None: declined, nobody
        to ask, or a day went by. The values reach this
        process and nothing else; they are never in the transcript.

        A function whose manifest does not declare ``credentials: true``
        raises ResourceDenied."""
        if self._credential is None:
            raise ResourceDenied(
                "Logins are not available here — the function must declare "
                "`credentials: true` in its manifest, and the invocation "
                "must run inside a chat.")
        answer = await self._credential(
            str(host or ""), [dict(f) for f in fields or [] if isinstance(f, dict)],
            str(account or "") or None, str(site or "") or None, bool(refresh))
        return dict(answer) if isinstance(answer, dict) else None

    async def llm(self, prompt: str, system: Optional[str] = None,
                  max_tokens: Optional[int] = None,
                  images: Optional[List[Any]] = None) -> "Completion":
        """One completion from the chat's configured model.

        The platform makes the call with the chat's model connection;
        its key never reaches agent code.

        The answer is text (a ``Completion``, a ``str``) that also knows
        how the model stopped: ``answer.cut`` is true when a token cap
        ended the reply before the model was done. ``max_tokens`` caps
        the reply; left out, the model stops when it is done, which is
        the right choice for a reply that is one object or a few lines.

        ``images`` puts pictures in front of the model beside the prompt:
        each is ``{"resource_id": …, "ref": …}`` naming a file this
        function may read (a scan, a photo). The platform reads the bytes
        under the function's own grant and hands them to the model; a
        model that cannot see pictures refuses with ResourceDenied."""
        if self._llm is None:
            raise ResourceDenied(
                "The platform model is not available here — the function "
                "must declare `llm: true` in its manifest, and the "
                "invocation must run inside a chat with a model."
            )
        messages = []
        if system:
            messages.append({"role": "system", "content": str(system)})
        messages.append({"role": "user", "content": str(prompt)})
        if images is not None and not (
                isinstance(images, (list, tuple))
                and all(isinstance(i, dict) for i in images)):
            # Said to the function, and not dropped: the model would be
            # asked without the picture and answer as if it had seen it.
            raise ValueError(
                "images is a list of {\"resource_id\": …, \"ref\": …} — "
                "a picture is named, not passed as bytes or as a bare ref")
        pictures = [dict(i) for i in images or []]
        if pictures:
            answer = await self._llm(messages, max_tokens, pictures)
        else:
            answer = await self._llm(messages, max_tokens)
        if isinstance(answer, Completion):
            return answer
        return Completion(answer, getattr(answer, "stop_reason", ""))


class ToolBase:
    """One manifest tool: shared integration concerns live on the
    instance; each function is a method named by its function id."""

    id: str = ""

    def __init__(self, agent: "AgentBase"):
        self.agent = agent
        self.logger = logging.getLogger(self.__class__.__name__)

    async def close(self) -> None:
        """Release whatever the tool holds (sessions, engines). Optional."""


class AgentBase:
    """The entrypoint class agent packages subclass.

    Receives the validated Manifest; builds the tool registry from
    ``tools()``; resolves canonical function names to bound methods —
    manifest-declared ones only, so undeclared methods are unreachable.
    """

    def __init__(self, manifest: Manifest):
        self.manifest = manifest
        self.logger = logging.getLogger(self.__class__.__name__)

        self._tools: Dict[str, ToolBase] = {}
        for tool in self.tools():
            tool_id = getattr(tool, "id", "") or ""
            if not tool_id:
                raise ValueError(
                    f"{tool.__class__.__name__} declares no tool id"
                )
            if tool_id in self._tools:
                raise ValueError(f"Duplicate tool id '{tool_id}'")
            self._tools[tool_id] = tool

    # ------------------------------------------------------------------
    def tools(self) -> List[ToolBase]:
        """Subclasses return their tool instances, one per manifest tool."""
        raise NotImplementedError

    @property
    def agent_id(self) -> str:
        return self.manifest.agent_id

    def tool(self, tool_id: str) -> Optional[ToolBase]:
        """A sibling tool instance — how tools share agent-level state."""
        return self._tools.get(tool_id)

    # ------------------------------------------------------------------
    def function(self, canonical_name: str) -> Optional[Callable]:
        """``agent.tool.function`` → the bound method, or None.

        Resolution goes through the manifest first: a method that exists in
        code but not in the manifest does not resolve, and a manifest
        function the code lacks returns None (the platform treats that as a
        broken package)."""
        declared = self.manifest.function(canonical_name)
        if declared is None:
            return None
        tool_spec, function_spec = declared

        tool = self._tools.get(tool_spec["id"])
        if tool is None:
            return None

        function_id = function_spec["id"]
        if keyword.iskeyword(function_id):
            # A function id like "import" cannot be a Python method name;
            # the PEP 8 convention (trailing underscore) implements it.
            function_id += "_"
        # A function is a method the tool's own class wrote. What every
        # tool has from ToolBase (`close`) is not one, though it has
        # the name: it would resolve where nothing was implemented.
        if getattr(ToolBase, function_id, None) is not None and \
                getattr(type(tool), function_id, None) is getattr(
                    ToolBase, function_id):
            return None
        method = getattr(tool, function_id, None)
        return method if callable(method) else None

    def missing_functions(self) -> List[str]:
        """Manifest functions the code does not implement — must be empty
        for the package to load."""
        return [
            name
            for name, _, _ in self.manifest.functions()
            if self.function(name) is None
        ]

    # ------------------------------------------------------------------
    async def close(self) -> None:
        for tool in self._tools.values():
            await tool.close()
