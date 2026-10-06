"""The chat's vocabulary, shared by the runtime, the backend and — by a
drift test (tests/test_chat_contract.py) — the page.

What the runtime sends toward a person is one of the events below, and
what a message carries is one of the parts. Both may say who produced
them: a ``source`` names the assistant, an installed agent (and the
call and job it spoke on), a helper, the scheduler, or the platform.

The backend validates events and parts where it records them and
refuses what does not fit; the simulated platform validates every
emission, so a malformed event fails the runtime's tests rather than
reaching a chat (docs/system/chat-session.md, "The vocabulary"). Additive
optional fields are compatible within a protocol version; removing or
changing one requires a new version.
"""

from __future__ import annotations

import json
from typing import Any, Annotated, Literal, Optional, Union, get_args

from pydantic import (
    BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator,
)

CHAT_PROTOCOL_VERSION = 2

# A plan is a short list of intentions, not a document. The bounds exist
# so a model that decides to think out loud cannot fill a chat with it —
# and they live here because the chat's live plan and the runtime that
# writes it have to agree on them.
PLAN_MAX_STEPS = 12
PLAN_STEP_MAX_CHARS = 200
#: Columns a table part may name.
COLUMNS_MAX = 24
#: What one display may hold — something a person reads at a glance,
#: not an export — and how many one call may offer.
DISPLAY_ROWS_MAX = 500
CHART_POINTS_MAX = 200
CHART_SERIES_MAX = 8
DISPLAYS_PER_CALL_MAX = 5
DISPLAY_MAX_BYTES = 262144
#: What one call may say for itself (call.post): a message, not a report.
#: How much a person may say in one message, in bytes of text. The
#: message is kept whole and so is the inbox event that carries it to
#: the assistant: more than this is refused at the door, before
#: anything is kept, and belongs in a file.
USER_TEXT_MAX_BYTES = 262144

POST_MAX_CHARS = 4000
POSTS_PER_CALL_MAX = 3
#: What an agent may ask a person (call.ask), and how long it waits.
QUESTION_MAX_CHARS = 1000
CHOICES_MAX = 8
CHOICE_MAX_CHARS = 100
ANSWER_MAX_CHARS = 2000
QUESTION_WAIT_SECONDS = 24 * 60 * 60
#: Code an agent puts before the person before it runs (call.propose):
#: the code whole, what it is for in words, and what it needs.
CODE_LANGUAGES = ("python", "javascript")
CODE_MAX_CHARS = 20_000
CODE_PURPOSE_MAX_CHARS = 600
CODE_WHERE_MAX_CHARS = 253
CODE_NEEDS_MAX = 30
CODE_NEED_MAX_CHARS = 253
CODE_REVIEW_MAX_CHARS = 600
#: A screen an agent shows (call.screen): one frame's size, and how many
#: input events one page message may carry.
SCREEN_FRAME_MAX_BYTES = 300_000
SCREEN_INPUT_EVENTS_MAX = 64
#: What a frame tells of the tabs behind its picture.
SCREEN_TABS_MAX = 20
SCREEN_TAB_TITLE_MAX_CHARS = 200
SCREEN_TAB_ADDRESS_MAX_CHARS = 300
#: A files question (the assistant's find_files): how many the card
#: proposes, and how many the person may choose — the composer's cap.
FILE_CANDIDATES_MAX = 5
FILES_CHOSEN_MAX = 20


class ChatModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Who produced it
# ---------------------------------------------------------------------------

class Source(ChatModel):
    """Who produced an event or a part. ``agent`` is the ref the platform
    routes by, ``agent_name`` what a person knows it as; ``call_id`` ties
    an agent's own progress to the call it was made on, ``job_id`` a
    background job's lines to the job, ``child`` a helper's to its
    thread."""
    kind: Literal["assistant", "agent", "helper", "scheduler", "system"]
    agent: Optional[str] = None
    agent_name: Optional[str] = None
    function: Optional[str] = None
    call_id: Optional[str] = None
    job_id: Optional[str] = None
    child: Optional[str] = None


def agent_source(agent: str, agent_name: str = "", function: str = "",
                 **ids: str) -> dict:
    """The source of anything an installed agent produced, with the
    empty fields left out — one shape wherever the runtime says it."""
    source = {"kind": "agent", "agent": agent, "agent_name": agent_name,
              "function": function, **ids}
    return {key: value for key, value in source.items() if value}


# ---------------------------------------------------------------------------
# Message parts
# ---------------------------------------------------------------------------

class MarkdownPart(ChatModel):
    type: Literal["markdown"]
    content: str = Field(min_length=1)
    source: Optional[Source] = None


class FilePart(ChatModel):
    type: Literal["file"]
    resource_ref: str = Field(min_length=1)
    filename: Optional[str] = None
    file_size: Optional[int] = Field(default=None, ge=0)
    file_type: Optional[str] = None
    #: The function that made it, for a file an agent produced.
    text: Optional[str] = None
    source: Optional[Source] = None


class StoredPart(ChatModel):
    """Rows or a chart held in the chat's storage, shown from there."""
    type: Literal["table", "graph"]
    storage_ref: str = Field(min_length=1)
    text: Optional[str] = None
    path: Optional[str] = None
    columns: Optional[list[Annotated[str, Field(min_length=1)]]] = Field(
        default=None, max_length=COLUMNS_MAX)
    source: Optional[Source] = None


class SuccessPart(ChatModel):
    """A verified write — machine state the page keeps and does not
    render."""
    type: Literal["success"]
    text: str = Field(min_length=1)
    storage_ref: Optional[str] = None
    source: Optional[Source] = None


PART_MODELS = (MarkdownPart, FilePart, StoredPart, SuccessPart)
MessagePart = Annotated[Union[PART_MODELS], Field(discriminator="type")]


class ChatMessage(ChatModel):
    message_id: str
    chat_id: str
    actor: Literal["user", "ai", "system", "parent"]
    parts: list[MessagePart]
    sequence: int
    thread: Optional[str] = None
    client_message_id: Optional[str] = None
    created_at: Optional[str] = None


class ChatInputCommand(ChatModel):
    """What the page sends to speak (the socket's AI:Chat:Input)."""
    protocol_version: Literal[CHAT_PROTOCOL_VERSION] = CHAT_PROTOCOL_VERSION
    client_message_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1)
    attachments: list[FilePart] = Field(default_factory=list, max_length=20)


class MessagePage(ChatModel):
    messages: list[dict[str, Any]]
    total: int
    next_before: Optional[int] = None
    has_more: bool = False


# ---------------------------------------------------------------------------
# Displays: what an agent's call offers to show
# ---------------------------------------------------------------------------
#
# A function offers a table or a chart with call.show (the SDK); the
# runtime checks it here, keeps it in chat storage, and names it on the
# call's result. It is an offer, not a message: only the model's
# say.show puts it in front of a person, as a table or graph part.

Cell = Union[str, int, float, bool, None]


class TableDisplay(ChatModel):
    kind: Literal["table"]
    title: str = Field(default="", max_length=120)
    #: Each row a record of plain values — a cell is text, a number,
    #: true/false or empty, never a structure.
    rows: list[dict[str, Cell]] = Field(min_length=1, max_length=DISPLAY_ROWS_MAX)
    columns: Optional[list[Annotated[str, Field(min_length=1)]]] = Field(
        default=None, max_length=COLUMNS_MAX)


class ChartSeries(ChatModel):
    name: str = Field(min_length=1, max_length=60)
    values: list[Optional[float]] = Field(min_length=1, max_length=CHART_POINTS_MAX)


class ChartDisplay(ChatModel):
    kind: Literal["chart"]
    chart_type: Literal["bar", "line", "pie"]
    title: str = Field(default="", max_length=120)
    labels: list[str] = Field(min_length=1, max_length=CHART_POINTS_MAX)
    series: list[ChartSeries] = Field(min_length=1, max_length=CHART_SERIES_MAX)

    @model_validator(mode="after")
    def _one_value_per_label(self) -> "ChartDisplay":
        for series in self.series:
            if len(series.values) != len(self.labels):
                raise ValueError(f"series '{series.name}' has {len(series.values)} "
                                 f"values for {len(self.labels)} labels — one "
                                 f"value per label")
        return self


Display = Annotated[Union[TableDisplay, ChartDisplay], Field(discriminator="kind")]
_DISPLAYS = TypeAdapter(Display)


def display_stored(spec: Any) -> tuple:
    """``(what storage keeps, None)`` for a display that fits, ``(None,
    why)`` for one that does not. What is kept is what the page already
    draws: a table as ``{rows, columns}``, a chart as the chart viewer's
    ``{chartData: {labels, datasets}, chartType, title}``."""
    try:
        display = _DISPLAYS.validate_python(spec)
    except ValidationError as exc:
        return None, _described(exc)
    if isinstance(display, TableDisplay):
        columns = display.columns or list(dict.fromkeys(
            key for row in display.rows for key in row))[:COLUMNS_MAX]
        stored = {"rows": [dict(row) for row in display.rows],
                  "columns": list(columns)}
    else:
        stored = {"chartData": {
                      "labels": list(display.labels),
                      "datasets": [{"label": series.name,
                                    "data": list(series.values)}
                                   for series in display.series]},
                  "chartType": display.chart_type, "title": display.title}
    size = len(json.dumps(stored, default=str).encode("utf-8"))
    if size > DISPLAY_MAX_BYTES:
        return None, (f"the display is {size} bytes; the limit is "
                      f"{DISPLAY_MAX_BYTES} — show fewer rows")
    return stored, None


# ---------------------------------------------------------------------------
# Events: what the runtime sends toward the chat
# ---------------------------------------------------------------------------

class EventModel(ChatModel):
    #: The sequence the durable record gave it, on live delivery.
    seq: Optional[int] = None
    chat_id: Optional[str] = None
    #: A helper's thread, on everything a helper emits.
    child: Optional[str] = None
    source: Optional[Source] = None


class MessageCreated(EventModel):
    event: Literal["message_created"]
    message: dict[str, Any]


class Working(EventModel):
    """The mind started advancing."""
    event: Literal["working"]


class Idle(EventModel):
    """The mind came to rest — the one frame that ends a wait."""
    event: Literal["idle"]


ActivityKind = Literal[
    "call_started",     # the assistant called an agent function
    "call_finished",    # ... and it came back, with status and duration
    "agent_progress",   # the agent's own line, on its call
    "job_started",      # a function started in the background
    "job_finished",     # ... and settled, with status and duration
    "helper_spawned",   # a helper was given a goal
    "helper_said",      # a helper's words, which never become a message
]


class Activity(EventModel):
    """One line of the work as it happens. Replaces ``progress``, whose
    single string carried five kinds of thing and no speaker."""
    event: Literal["activity"]
    kind: ActivityKind
    text: str = Field(min_length=1)
    #: How a call or job ended: the executor's success/error, a job's
    #: done/failed/cancelled.
    status: Optional[str] = None
    duration_ms: Optional[int] = Field(default=None, ge=0)


class PlanUpdated(EventModel):
    event: Literal["plan_updated"]
    steps: list[dict[str, Any]]


class MemorySaved(EventModel):
    event: Literal["memory_saved"]
    text: str


class ApprovalRequested(EventModel):
    event: Literal["approval_requested"]
    approval_id: str = Field(min_length=1)
    job_id: str = ""
    function: str
    permission_level: int
    inputs: dict[str, Any] = Field(default_factory=dict)
    #: Who is asking: the ref routed by, and the name decided by.
    agent: Optional[str] = None
    agent_name: Optional[str] = None


class FileChoice(ChatModel):
    """One file a files question proposes: what the page shows, and the
    ref the answer names it by."""
    resource_ref: str = Field(min_length=1)
    filename: str = ""
    file_type: str = ""
    file_size: int = Field(default=0, ge=0)
    #: where it came from, in the person's words: the Files page, a
    #: chat, an agent
    source: str = ""
    created_at: str = ""


class CredentialField(ChatModel):
    """One field a credential card asks for."""
    name: str = Field(min_length=1, max_length=60)
    label: str = Field(default="", max_length=80)
    type: Literal["text", "secret"] = "text"
    required: bool = True
    #: False: asked every time, never stored — a one-time code.
    remember: bool = True


class CredentialInstance(ChatModel):
    resource_ref: str = Field(min_length=1)
    name: str = ""
    account: str = ""


class CredentialAsk(ChatModel):
    """An agent asking for a login it needs as it works (call.credential):
    which card, for which host and site, in whose name.

    ``entry`` asks for the fields (the typed values go to the vault, the
    card closes with the row's ref); ``consent`` asks whether this agent
    may use a saved login on this site; ``choose`` asks which of several
    saved logins; ``once`` asks for the fields that are never stored."""
    mode: Literal["entry", "consent", "choose", "once"]
    host: str = Field(min_length=1, max_length=253)
    site: str = ""
    account: str = ""
    agent_ref: str = ""
    resource_id: str = ""
    definition_ref: str = ""
    resource_ref: str = ""
    #: entry: an existing row is being completed or refreshed
    existing: bool = False
    fields: list[CredentialField] = Field(default_factory=list, max_length=20)
    instances: list[CredentialInstance] = Field(default_factory=list, max_length=20)


CodeNeed = Annotated[str, Field(min_length=1, max_length=CODE_NEED_MAX_CHARS)]


class CodeReview(ChatModel):
    """What the assistant made of the code before the person saw it:
    ``agrees`` when it does what its purpose says and needs nothing it
    did not name, ``differs`` when it does not, ``unread`` when no
    review could be made. The note is for the person, in plain words."""
    verdict: Literal["agrees", "differs", "unread"]
    note: str = Field(default="", max_length=CODE_REVIEW_MAX_CHARS)


class CodeAsk(ChatModel):
    """Code an agent wants to run (call.propose), put before the person
    whole: the code, what it is for, where it runs, and what it needs —
    packages to install, hosts to reach, credentials to be handed,
    files to read. The answer is ``allow`` or ``deny``."""
    language: Literal["python", "javascript"]
    code: str = Field(min_length=1, max_length=CODE_MAX_CHARS)
    purpose: str = Field(min_length=1, max_length=CODE_PURPOSE_MAX_CHARS)
    #: where it runs, in the person's words: a site for a script in a
    #: page; empty for the agent's own sandbox
    where: str = Field(default="", max_length=CODE_WHERE_MAX_CHARS)
    packages: list[CodeNeed] = Field(default_factory=list, max_length=CODE_NEEDS_MAX)
    hosts: list[CodeNeed] = Field(default_factory=list, max_length=CODE_NEEDS_MAX)
    credentials: list[CodeNeed] = Field(default_factory=list, max_length=CODE_NEEDS_MAX)
    files: list[CodeNeed] = Field(default_factory=list, max_length=CODE_NEEDS_MAX)
    review: Optional[CodeReview] = None


def code_asked(spec: Any) -> tuple:
    """``(the card's code, None)`` for a proposal that fits, ``(None,
    why)`` for one that does not."""
    try:
        return CodeAsk.model_validate(spec).model_dump(exclude_none=True), None
    except ValidationError as exc:
        return None, _described(exc)


class QuestionAsked(EventModel):
    """An agent asking the person something mid-call (call.ask): a card,
    answered by a choice or in the person's own words. The assistant's
    own files question (find_files) is the same card, expecting
    ``files``: the candidates it found ride on it, and the answer is the
    refs the person chose. Code an agent wants to run (call.propose) is
    the same card again, expecting ``code``."""
    event: Literal["question_asked"]
    approval_id: str = Field(min_length=1)
    job_id: str = ""
    function: str = ""
    agent: Optional[str] = None
    agent_name: Optional[str] = None
    question: str = Field(min_length=1, max_length=QUESTION_MAX_CHARS)
    choices: list[str] = Field(default_factory=list, max_length=CHOICES_MAX)
    #: what kind of answer the agent wants: words (the default), a file
    #: the person attaches, answered with its ref, or files they choose
    #: from what they can see, answered with their refs
    expects: Literal["", "text", "file", "files", "credential", "code"] = ""
    #: a files question: what the assistant found, and what it looked for
    candidates: list[FileChoice] = Field(default_factory=list,
                                         max_length=FILE_CANDIDATES_MAX)
    query: str = ""
    #: a credential question: which card, for which host, in whose name
    credential: Optional[CredentialAsk] = None
    #: a code question: the code, what it is for and what it needs
    code: Optional[CodeAsk] = None


class QuestionClosed(EventModel):
    """A question settled: answered, or expired — nobody answered in
    time, or the call that asked it had ended."""
    event: Literal["question_closed"]
    approval_id: str = Field(min_length=1)
    status: Literal["answered", "expired"]


class ScheduleSet(EventModel):
    event: Literal["schedule_set"]
    schedule: dict[str, Any]


class ScheduleRemoved(EventModel):
    event: Literal["schedule_removed"]
    schedule_id: str


class Sleeping(EventModel):
    """The assistant paused in the middle of work and will be woken at
    ``until`` (epoch seconds); ``until`` null when a stop ended it."""
    event: Literal["sleeping"]
    until: Optional[float] = None
    why: str = ""


class Hello(EventModel):
    """The door's present tense on attach. Socket-only."""
    event: Literal["hello"]
    protocol_version: int
    #: whether a turn is under way as the audience arrives
    working: bool = False
    #: ``{until, why}`` while the assistant sleeps
    sleeping: Optional[dict[str, Any]] = None
    active_jobs: list[dict[str, Any]] = Field(default_factory=list)
    pending_approvals: list[dict[str, Any]] = Field(default_factory=list)
    plan: list[dict[str, Any]] = Field(default_factory=list)


class AgentStatus(EventModel):
    """An agent being prepared before the hello. Socket-only."""
    event: Literal["agent_status"]
    agent: str
    name: str
    phase: Literal["pulling", "installing", "ready", "failed"]
    text: str


class ScreenTab(ChatModel):
    """One of the things open behind a screen's picture: a browser's
    tab, an application's window."""
    #: its place among them, from 1 — what the person's hand names
    index: int = Field(ge=1, le=1000)
    title: str = Field(default="", max_length=SCREEN_TAB_TITLE_MAX_CHARS)
    address: str = Field(default="", max_length=SCREEN_TAB_ADDRESS_MAX_CHARS)
    #: the one the picture shows
    active: bool = False


class ScreenFrame(EventModel):
    """One frame of a screen an agent is showing (call.screen): the
    browser it drives, as a picture. Socket-only — never recorded, since
    a replay of pictures is nothing anyone asked for."""
    event: Literal["screen_frame"]
    call_id: str = Field(min_length=1)
    image_base64: str = Field(min_length=1)
    mime: Literal["image/jpeg", "image/png"] = "image/jpeg"
    width: int = Field(ge=1, le=8192)
    height: int = Field(ge=1, le=8192)
    #: the frame's own count, from the agent — not ``seq``, which is
    #: the durable record's number and never given to a picture
    frame: int = 0
    #: whether the person holds control at the moment of this frame
    taken: bool = False
    #: what is open behind the picture, in its order; empty where the
    #: agent tells of none
    tabs: list[ScreenTab] = Field(default_factory=list, max_length=SCREEN_TABS_MAX)


class ScreenClosed(EventModel):
    """The agent stopped showing its screen, or the call ended."""
    event: Literal["screen_closed"]
    call_id: str = Field(min_length=1)


class ErrorEvent(EventModel):
    event: Literal["error"]
    detail: str


class Stopped(EventModel):
    """The kill switch answered: everything the chat was doing ended
    where it stood, and this is what went."""
    event: Literal["stopped"]
    jobs: int = 0
    children: int = 0
    cards: int = 0
    detail: str = ""


EVENT_MODELS = (
    MessageCreated, Working, Idle, Activity, PlanUpdated, MemorySaved,
    ApprovalRequested, QuestionAsked, QuestionClosed, ScheduleSet,
    ScheduleRemoved, Sleeping, Hello, AgentStatus, ScreenFrame, ScreenClosed, ErrorEvent,
    Stopped,
)
ChatEvent = Annotated[Union[EVENT_MODELS], Field(discriminator="event")]

EVENT_NAMES = tuple(get_args(model.model_fields["event"].annotation)[0]
                    for model in EVENT_MODELS)
PART_TYPES = tuple(value for model in PART_MODELS
                   for value in get_args(model.model_fields["type"].annotation))
#: Frames the backend adds on its own side of the relay — the page
#: hears them, the runtime never sends them.
RELAY_EVENTS = ("runtime_unavailable", "runtime_disconnected", "invalid_input",
                "work_stopped")
#: Frames the runtime delivers to an open chat without a record — news
#: for whoever is watching, with nothing to replay.
DELIVERED_EVENTS = ("chat_titled", "screen_unavailable")
#: Retired names a replay of the last few hundred events may still
#: carry — the page reads them, nothing sends them any more.
RETIRED_EVENTS = ("progress",)

_EVENTS = TypeAdapter(ChatEvent)
_PARTS = TypeAdapter(MessagePart)


def _described(exc: ValidationError) -> str:
    error = exc.errors()[0]
    where = ".".join(str(part) for part in error.get("loc", ()))
    return f"{where}: {error.get('msg')}" if where else str(error.get("msg"))


def event_error(event: Any) -> Optional[str]:
    """Why an event does not fit the vocabulary, or None when it does."""
    try:
        _EVENTS.validate_python(event)
    except ValidationError as exc:
        return _described(exc)
    return None


def part_error(part: Any) -> Optional[str]:
    """Why a message part does not fit, or None when it does."""
    try:
        _PARTS.validate_python(part)
    except ValidationError as exc:
        return _described(exc)
    return None
