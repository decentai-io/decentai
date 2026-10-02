"""The Session — the assistant embodied (docs/system/assistant.md).

Real agents in real workers, the sim's session services standing in for
the backend. What these tests pin is the embodiment's contract: messages
persist, events stream, approvals park one job and never the mind, and
a mind whose process dies is hydrated whole by the next one.
"""

import asyncio
import json
from pathlib import Path

from ai_runtime.chat import Session
from ai_runtime.llms import FakeConnector
from ai_runtime.reasoning.state import RUNNING, AssistantState, Job
from ai_runtime.tests.fixture_agents import load_agents
from contracts.chat import event_error
from sim.session_services import SimSessionServices

AGENTS_DIR = Path(__file__).resolve().parent / "fixtures" / "agents"

SYNC_SECRET = {"notebook__connection": {
    "base_url": "https://sim.invalid", "api_token": "token",
}}


def run(awaitable):
    return asyncio.run(awaitable)


def verified(message):
    """The writes a message records — `success` parts the page keeps
    out of the conversation."""
    return [p["text"] for p in message.get("parts", [])
            if p.get("type") == "success"]


def action(**kwargs):
    return json.dumps(kwargs)


def build(script, services=None, **kwargs):
    agents, errors = load_agents(AGENTS_DIR)
    assert errors == {}
    services = services or SimSessionServices()
    return Session("chat_1", agents, FakeConnector(script), services,
                   **kwargs), services


async def approval_open(services, timeout=5.0):
    """Wait until the (held) approval card exists."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not services.approvals:
        assert asyncio.get_running_loop().time() < deadline, "no approval"
        await asyncio.sleep(0.02)
    return next(iter(services.approvals))


PNG = b"\x89PNG\r\n\x1a\n" + b"fictional pixels"


class RefusingConnector(FakeConnector):
    """A model that will not look at pictures — and says so the way a
    provider does, in an error nobody standardized."""

    def __init__(self, responses):
        super().__init__(responses)
        self.refusals = 0

    async def chat(self, messages, max_tokens=None, tools=None):
        if any(isinstance(m.get("content"), list) for m in messages):
            self.refusals += 1
            raise RuntimeError("400: unsupported content type 'image_url'")
        return await super().chat(messages, max_tokens, tools)


class TestPicturesReachTheModel:
    """A person pastes a screenshot and expects to be understood. The
    bytes are fetched at the model call and never kept in the mind."""

    async def _attach(self, session, services, text="what is this?",
                      raw=PNG, mime="image/png"):
        stored = await services.provider.create_file(
            "chat_attachment", "screenshot.png", raw)
        await session.open()
        await session.deliver_user(text, parts=[{
            "type": "file", "resource_ref": stored["resource_ref"],
            "filename": "screenshot.png", "file_type": mime}])
        await session.wait_idle()
        return stored["resource_ref"]

    def test_a_document_attached_is_read_by_the_same_door(self):
        """read_file goes through the download that fetches a picture:
        the person's own delegation, no agent, no grant."""
        session, services = build([])
        # The script needs the ref, so attach first and script after.
        async def scenario():
            stored = await services.provider.create_file(
                "uploads", "regions.csv", b"region,total\nnorth,12\nsouth,30\n")
            session.connector.responses = [
                action(action="read_file", file_ref=stored["resource_ref"]),
                action(action="say", text="Two regions.", final=True),
            ]
            await session.open()
            await session.deliver_user("summarize it", parts=[{
                "type": "file", "resource_ref": stored["resource_ref"],
                "filename": "regions.csv", "file_type": "text/csv"}])
            await session.wait_idle()

        run(scenario())
        observed = next(m["content"] for m in session.assistant.state.messages
                        if m["content"].startswith("OBSERVATION") and "regions" in m["content"])
        assert "north,12" in observed

    def test_the_picture_travels_as_a_block_and_not_in_the_transcript(self):
        session, services = build([
            action(action="say", text="A bar chart."),
            action(action="finish"),
        ])
        ref = run(self._attach(session, services))

        spoken = [m for m in session.assistant.connector.calls[0]["messages"]
                  if isinstance(m.get("content"), list)]
        assert len(spoken) == 1, "the words and the picture travel together"
        words, picture = spoken[0]["content"]
        assert words["type"] == "text" and "what is this?" in words["text"]
        assert picture["type"] == "image_url"
        assert picture["image_url"]["url"].startswith("data:image/png;base64,")

        # The mind keeps the REF. Persisted every beat under a size cap,
        # a transcript carrying the bytes would stop saving at all — and
        # a failed save only warns.
        kept = session.assistant.state.messages
        assert any(m.get("images") for m in kept)
        assert ref in json.dumps(kept)
        assert "content_base64" not in json.dumps(kept)

    def test_the_frame_tells_the_model_a_picture_needs_no_agent(self):
        """The attachment rule said to hand every file to a function and
        never to claim reading one no function returned. With the picture
        in front of it, a model obeyed that and told the person it had no
        agent to read images. The frame now says a picture is seen."""
        from ai_runtime.prompts import Prompts
        frame = Prompts.text("assistant")
        assert "A picture the user attached" in frame
        assert "no agent is needed to read it" in frame

    def test_a_picture_too_large_is_left_behind_and_said_so(self):
        session, services = build([
            action(action="say", text="I could not see it."),
            action(action="finish"),
        ])
        huge = b"\x89PNG\r\n\x1a\n" + b"x" * (4 * 1024 * 1024)
        run(self._attach(session, services, raw=huge))

        sent = session.assistant.connector.calls[0]["messages"]
        assert not any(isinstance(m.get("content"), list) for m in sent)
        # And the model is told, so it cannot answer as though it saw.
        assert any("could not be shown" in str(m.get("content") or "")
                   for m in sent)

    def test_a_model_that_refuses_pictures_is_asked_again_without_them(self):
        """Capability is learned, not declared: the request is made and
        the refusal is the answer. The turn continues on the words."""
        agents, errors = load_agents(AGENTS_DIR)
        assert errors == {}
        services = SimSessionServices()
        # The connector is the session's from the start: the mind is not
        # built until open(), so it cannot be swapped in afterwards.
        connector = RefusingConnector([
            action(action="say", text="I cannot see the image."),
            action(action="finish"),
        ])
        session = Session("chat_1", agents, connector, services)
        run(self._attach(session, services))

        assert connector.refusals == 1, "refused once, then never retried"
        assert session.assistant._images_allowed is False
        # The turn finished and the answer was persisted: a model that
        # cannot see the picture still answers from the words.
        reply = services.messages["chat_1"][1]
        assert reply["actor"] == "ai"
        assert reply["parts"][0]["content"] == "I cannot see the image."
        # Every later beat went without pictures, and said why.
        assert all(not any(isinstance(m.get("content"), list)
                           for m in call["messages"])
                   for call in connector.calls)


class TestConversation:
    def test_a_message_in_becomes_work_and_messages_out(self):
        session, services = build([
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.note.save",
                   inputs={"notebook": "work", "title": "Ship"}),
            action(action="say", text="Saved."),
            action(action="finish"),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("note: ship")
            await session.wait_idle()

        run(scenario())
        # Both sides of the conversation persisted, in order.
        actors = [m["actor"] for m in services.messages["chat_1"]]
        assert actors == ["user", "ai"]
        # The say carried its verified evidence — as a recorded part,
        # beside the model's own words.
        reply = services.messages["chat_1"][1]
        assert reply["parts"][0]["content"] == "Saved."
        assert any(p.get("type") == "success" and "Save Note" in p["text"]
                   for p in reply["parts"])
        # The record is real, and the state was persisted.
        assert len(services.provider.data.get("notebook__note", {})) == 1
        assert services.states["chat_1"]["trace"]

    def test_a_call_is_told_as_one_thing_under_its_agents_name(self):
        session, services = build([
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.note.save",
                   inputs={"notebook": "work", "title": "Ship"}),
            action(action="say", text="Saved."),
            action(action="finish"),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("note: ship")
            await session.wait_idle()

        run(scenario())
        activity = [e for e in services.events if e["event"] == "activity"]
        started = next(e for e in activity if e["kind"] == "call_started")
        finished = next(e for e in activity if e["kind"] == "call_finished")
        own = [e for e in activity if e["kind"] == "agent_progress"]
        call_id = started["source"]["call_id"]
        assert started["source"]["kind"] == "agent"
        assert started["source"]["agent"] == "notebook"
        assert started["source"]["agent_name"]
        # The start, the agent's own lines and the finish share one call.
        assert finished["source"]["call_id"] == call_id
        assert finished["status"] == "success"
        assert finished["duration_ms"] >= 0
        assert own and all(e["source"]["call_id"] == call_id for e in own)
        # And what the call produced says whose it is.
        reply = services.messages["chat_1"][1]
        success = next(p for p in reply["parts"] if p.get("type") == "success")
        assert success["source"]["agent"] == "notebook"
        # Nothing speaks the retired word.
        assert not [e for e in services.events if e["event"] == "progress"]

    def test_what_a_call_offers_to_show_is_kept_and_named(self):
        session, services = build([
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.note.save",
                   inputs={"notebook": "work", "title": "Ship"}),
            action(action="invoke", function="notebook.note.find",
                   inputs={"notebook": "work"}),
            action(action="say", text="One note."),
            action(action="finish"),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("note ship, then list")
            await session.wait_idle()

        run(scenario())
        entry = next(e for e in session.assistant.state.trace
                     if e["function"] == "notebook.note.find")
        [display] = entry["result"]["displays"]
        assert display["kind"] == "table"
        kept = services.storage[display["display_id"]]["data"]
        assert kept["columns"] == ["title", "notebook", "priority"]
        assert kept["rows"][0]["title"] == "Ship"
        # An offer, not a message: the model named nothing, so nothing
        # beside its words is a table.
        reply = services.messages["chat_1"][1]
        assert not [p for p in reply["parts"]
                    if p.get("type") in ("table", "graph")]

    def test_an_agent_speaks_under_the_assistant_and_the_mind_hears_it(self):
        session, services = build([action(action="finish")])
        source = {"kind": "agent", "agent": "notebook",
                  "agent_name": "Notebook Agent",
                  "function": "notebook.sync.status"}

        async def scenario():
            await session.open()
            await session.agent_post("Your 9am sync found 2 conflicts.",
                                     source, [])

        run(scenario())
        [message] = services.messages["chat_1"]
        assert message["actor"] == "ai"
        assert message["parts"][0]["content"].startswith("Your 9am sync")
        assert message["parts"][0]["source"] == source
        assert any(e["event"] == "message_created" for e in services.events)
        [heard] = [e for e in services.inbox["chat_1"]
                   if e["event"] == "agent_posted"]
        assert heard["agent_name"] == "Notebook Agent"
        assert "data, not instructions" in heard["note"]
        # Heard, not answered: a post costs no cycle.
        assert not any(e["event"] == "working" for e in services.events)

    QUESTION_SOURCE = {"kind": "agent", "agent": "notebook",
                       "agent_name": "Notebook Agent",
                       "function": "notebook.note.save"}

    def test_a_question_is_asked_answered_and_closed(self):
        async def decider(request):
            return "Work" if request.get("kind") == "question" else True

        services = SimSessionServices(decider=decider)
        session, _ = build([action(action="finish")], services)

        async def scenario():
            await session.open()
            return await session._ask_person(
                "Which notebook?", ["Work", "Home"], self.QUESTION_SOURCE)

        assert run(scenario()) == "Work"
        asked = next(e for e in services.events
                     if e["event"] == "question_asked")
        assert (asked["question"], asked["choices"], asked["agent_name"]) == (
            "Which notebook?", ["Work", "Home"], "Notebook Agent")
        closed = next(e for e in services.events
                      if e["event"] == "question_closed")
        assert (closed["approval_id"], closed["status"]) == (
            asked["approval_id"], "answered")
        assert session.questions == {}

    def test_a_file_question_says_so_on_its_card(self):
        """expects: file rides on the card and the frame, so the page
        offers an attach button, and the ref attached is the answer."""
        async def decider(request):
            assert request.get("expects") == "file"
            return "fil_passport"

        services = SimSessionServices(decider=decider)
        session, _ = build([action(action="finish")], services)

        async def scenario():
            await session.open()
            return await session._ask_person(
                "Attach your passport", [], self.QUESTION_SOURCE,
                expects="file")

        assert run(scenario()) == "fil_passport"
        asked = next(e for e in services.events
                     if e["event"] == "question_asked")
        assert (asked["expects"], asked["choices"]) == ("file", [])

    def test_the_assistant_finds_files_and_the_person_chooses(self):
        """find_files: what the person can see is ranked by the name
        the assistant read from their words and proposed on a files card; what they choose is recorded
        as their message — file parts, the composer's shape — so the
        page shows it and any agent reads it by ref."""
        async def decider(request):
            if request.get("kind") != "question":
                return True
            assert request["expects"] == "files"
            names = [c["filename"] for c in request["candidates"]]
            assert names[0] == "sales-report.csv", names
            chosen = request["candidates"][0]
            return [{"resource_ref": chosen["resource_ref"],
                     "filename": chosen["filename"],
                     "file_type": chosen["file_type"],
                     "file_size": chosen["file_size"]}]

        services = SimSessionServices(decider=decider)
        session, _ = build([action(action="finish")], services)

        async def scenario():
            await services.provider.create_file("chat_attachment", "notes.txt", b"n")
            await services.provider.create_file("uploads", "sales-report.csv", b"a,b")
            await session.open()
            return await session._find_files(
                "the sales report", ["sales", "report"])

        outcome = run(scenario())
        assert outcome["status"] == "chosen"
        [chosen] = outcome["files"]
        assert chosen["filename"] == "sales-report.csv"
        asked = next(e for e in services.events if e["event"] == "question_asked")
        assert asked["expects"] == "files" and asked["query"] == "the sales report"
        assert asked["source"] == {"kind": "assistant"}
        assert [c["filename"] for c in asked["candidates"]][0] == "sales-report.csv"
        # Their choice is their message: a file part, no words.
        [message] = services.messages["chat_1"]
        assert message["actor"] == "user"
        assert [(p["type"], p["resource_ref"]) for p in message["parts"]] == [
            ("file", chosen["resource_ref"])]
        assert any(e["event"] == "message_created" for e in services.events)
        assert session.questions == {}

    def test_a_files_question_answered_with_none_is_a_decline(self):
        async def decider(request):
            return [] if request.get("kind") == "question" else True

        services = SimSessionServices(decider=decider)
        session, _ = build([action(action="finish")], services)

        async def scenario():
            await session.open()
            return await session._find_files("anything")

        assert run(scenario()) == {"status": "declined", "files": []}
        assert services.messages.get("chat_1", []) == []
        closed = next(e for e in services.events if e["event"] == "question_closed")
        assert closed["status"] == "answered"

    LOGIN_SOURCE = {"kind": "agent", "agent": "agt_browser",
                    "agent_name": "Browser", "function": "browser.login"}
    LOGIN_FIELDS = [{"name": "email", "label": "Email", "type": "text",
                     "required": True, "remember": True},
                    {"name": "password", "label": "Password", "type": "secret",
                     "required": True, "remember": True}]

    def test_a_login_is_typed_once_and_then_found(self):
        """call.credential, first time: an entry card; the person types,
        the sim's vault takes it as the backend would, the card answers
        with the row's ref, and the values reach the function. Second
        time: no card at all."""
        services = SimSessionServices()

        async def decider(request):
            if request.get("kind") != "question":
                return True
            ask = request["credential"]
            assert (ask["mode"], ask["host"], ask["site"]) == (
                "entry", "atlassian.com", "acme.atlassian.net")
            assert [f["name"] for f in ask["fields"]] == ["email", "password"]
            assert "password" not in str(request.get("question"))
            services.logins[("atlassian.com", "a@x.example")] = {
                "resource_ref": "sec_atl", "values": {"email": "a@x.example",
                                                      "password": "pw"},
                "consents": {"agt_browser@acme.atlassian.net"}}
            return {"resource_ref": "sec_atl"}

        services.decider = decider
        session, _ = build([action(action="finish")], services)

        async def scenario():
            await session.open()
            first = await session._credential(
                "atlassian.com", self.LOGIN_FIELDS, None, "acme.atlassian.net",
                False, self.LOGIN_SOURCE)
            second = await session._credential(
                "atlassian.com", self.LOGIN_FIELDS, None, "acme.atlassian.net",
                False, self.LOGIN_SOURCE)
            return first, second

        first, second = run(scenario())
        assert first == {"email": "a@x.example", "password": "pw",
                         "host": "atlassian.com", "account": "a@x.example"}
        assert second == first
        cards = [e for e in services.events if e["event"] == "question_asked"]
        assert len(cards) == 1 and cards[0]["expects"] == "credential"
        assert cards[0]["credential"]["mode"] == "entry"
        assert "pw" not in json.dumps(cards)
        assert services.credential_uses == [("sec_atl", "agt_browser", "acme.atlassian.net")] * 2

    def test_consent_choice_and_a_code_each_get_their_card(self):
        services = SimSessionServices()
        services.logins[("amazon.com", "one@x.example")] = {
            "resource_ref": "sec_one", "values": {"email": "one@x.example", "password": "p1"},
            "consents": {"agt_browser@www.amazon.com"}}
        services.logins[("amazon.com", "two@x.example")] = {
            "resource_ref": "sec_two", "values": {"email": "two@x.example", "password": "p2"},
            "consents": set()}
        modes = []

        async def decider(request):
            if request.get("kind") != "question":
                return True
            ask = request["credential"]
            modes.append(ask["mode"])
            if ask["mode"] == "choose":
                assert sorted(i["account"] for i in ask["instances"]) == [
                    "one@x.example", "two@x.example"]
                return "sec_two"
            if ask["mode"] == "consent":
                services.logins[("amazon.com", "two@x.example")]["consents"].add(
                    "agt_browser@www.amazon.com")
                return "allow"
            if ask["mode"] == "once":
                assert [f["name"] for f in ask["fields"]] == ["otp"]
                return {"otp": "123456"}
            raise AssertionError(ask["mode"])

        services.decider = decider
        session, _ = build([action(action="finish")], services)
        fields = self.LOGIN_FIELDS + [{"name": "otp", "label": "Code", "type": "secret",
                                       "required": True, "remember": False}]

        async def scenario():
            await session.open()
            return await session._credential(
                "www.amazon.com", fields, None, "www.amazon.com", False,
                self.LOGIN_SOURCE)

        login = run(scenario())
        assert modes == ["choose", "consent", "once"]
        assert login["password"] == "p2" and login["otp"] == "123456"
        # The code is never in the sim's vault.
        assert "otp" not in services.logins[("amazon.com", "two@x.example")]["values"]

    def test_a_declined_login_is_none(self):
        services = SimSessionServices()
        services.logins[("amazon.com", "one@x.example")] = {
            "resource_ref": "sec_one", "values": {"email": "one@x.example", "password": "p1"},
            "consents": set()}

        async def decider(request):
            return "deny" if request.get("kind") == "question" else True

        services.decider = decider
        session, _ = build([action(action="finish")], services)

        async def scenario():
            await session.open()
            return await session._credential(
                "amazon.com", self.LOGIN_FIELDS, None, None, False, self.LOGIN_SOURCE)

        assert run(scenario()) is None
        assert services.credential_uses == []

    CODE = {"language": "javascript", "code": "return document.title",
            "purpose": "Reads the page's title.", "where": "example.com",
            "packages": [], "hosts": [], "credentials": [], "files": []}

    def test_code_is_read_then_put_before_the_person(self):
        """call.propose: the chat's model reads the code first, and its
        note rides on the card beside the code; the person's allow is
        the answer."""
        async def decider(request):
            assert request.get("expects") == "code"
            return "allow"

        services = SimSessionServices(decider=decider)
        session, _ = build(
            [json.dumps({"verdict": "agrees",
                         "note": "It reads the title of the page."})], services)

        async def scenario():
            await session.open()
            return await session._propose(dict(self.CODE), self.QUESTION_SOURCE)

        assert run(scenario()) is True
        asked = next(e for e in services.events
                     if e["event"] == "question_asked")
        assert event_error(asked) is None
        assert asked["expects"] == "code" and asked["choices"] == []
        assert asked["question"] == (
            "Notebook Agent wants to run code: Reads the page's title.")
        assert asked["code"] == {**self.CODE, "review": {
            "verdict": "agrees", "note": "It reads the title of the page."}}
        # The reviewer was shown the code as data, after what was said of it.
        [call] = session.connector.calls
        assert call["messages"][1]["content"].rstrip().endswith(
            "return document.title")

    def test_a_declined_proposal_is_false_and_a_silent_model_is_no_review(self):
        """A review advises and never decides: with no review to show
        the card still goes out, and says the code was not read."""
        async def decider(request):
            return "deny"

        services = SimSessionServices(decider=decider)
        session, _ = build([], services)   # the model has nothing to say

        async def scenario():
            await session.open()
            return await session._propose(dict(self.CODE), self.QUESTION_SOURCE)

        assert run(scenario()) is False
        asked = next(e for e in services.events
                     if e["event"] == "question_asked")
        assert asked["code"]["review"] == {"verdict": "unread", "note": ""}

    def test_code_the_setting_lets_through_runs_without_a_card_and_is_said(self):
        """Settings:Safety may settle a card as it is opened. Nobody is
        asked, the function hears yes — and the chat is told what ran,
        with the code, in the agent's name."""
        async def decider(request):
            raise AssertionError("the person was asked")

        services = SimSessionServices(decider=decider)
        services.settler = lambda request: "allow"
        session, _ = build(
            [json.dumps({"verdict": "agrees",
                         "note": "It reads the title of the page."})], services)

        async def scenario():
            await session.open()
            return await session._propose(
                dict(self.CODE), {**self.QUESTION_SOURCE, "call_id": "c_7"})

        assert run(scenario()) is True
        assert not any(e["event"] in ("question_asked", "question_closed")
                       for e in services.events)
        # The card is on the record, with the call that asked for it.
        [card] = services.approvals.values()
        assert card["request"]["call_id"] == "c_7" and card["settled"] is True
        [message] = services.messages["chat_1"]
        said = message["parts"][0]
        assert said["content"].startswith(
            "**Ran without asking**, as the Safety setting allows. "
            "Reads the page's title.")
        assert "_It reads the title of the page._" in said["content"]
        assert "```javascript\nreturn document.title\n```" in said["content"]
        assert said["source"]["agent_name"] == "Notebook Agent"
        assert any(e["event"] == "message_created" for e in services.events)

    def test_a_long_program_let_through_is_said_by_its_beginning(self):
        services = SimSessionServices()
        services.settler = lambda request: "allow"
        session, _ = build(['{"verdict": "agrees", "note": ""}'], services)
        code = {**self.CODE, "language": "python", "where": "",
                "code": "\n".join(f"print({n})" for n in range(40))}

        async def scenario():
            await session.open()
            return await session._propose(code, self.QUESTION_SOURCE)

        assert run(scenario()) is True
        said = services.messages["chat_1"][0]["parts"][0]["content"]
        assert "print(24)" in said and "print(25)" not in said
        assert "…and 15 more lines." in said

    def test_the_deployments_setting_reaches_the_executor_and_moves_with_it(self):
        services = SimSessionServices()
        session, _ = build([action(action="finish")], services,
                           safety={"blocked_sites": ["example.org"]})

        async def scenario():
            await session.open()

        run(scenario())
        executor = session.assistant.executor
        assert executor.safety == {"blocked_sites": ["example.org"]}
        session.adopt(session.roster, 1, safety={
            "blocked_sites": [], "packages": "listed", "allowed_packages": ["pandas"]})
        assert executor.safety["allowed_packages"] == ["pandas"]
        assert executor._listed_packages() == ["pandas"]

    def test_a_question_nobody_answers_expires(self):
        session, services = build([action(action="finish")])
        session.QUESTION_WAIT_SECONDS = 0.05

        async def scenario():
            await session.open()
            return await session._ask_person("Anyone?", [],
                                             self.QUESTION_SOURCE)

        assert run(scenario()) is None
        [card] = services.approvals.values()
        assert card["status"] == "expired"
        assert any(e["event"] == "question_closed"
                   and e["status"] == "expired" for e in services.events)

    def test_a_question_left_by_a_dead_process_is_closed_when_the_next_session_opens(self):
        """The process that asked died; nothing waits for the answer. The
        next session finds the card still open on the record, expires
        it and tells the page — another chat's question is left alone."""
        services = SimSessionServices()
        first, _ = build([action(action="finish")], services)

        async def scenario():
            await first.open()
            orphan = await services.open_approval("chat_1", {
                "kind": "question", "function": "agt_x.browse.run",
                "agent_id": "agt_x", "agent_name": "Browser",
                "question": "Take over?", "choices": ["Done", "Stop"]})
            elsewhere = await services.open_approval("chat_2", {
                "kind": "question", "function": "f", "agent_id": "a",
                "agent_name": "A", "question": "Other chat?", "choices": []})
            # The next process for this chat: the first is gone.
            second, _ = build([action(action="finish")], services)
            await second.open()
            return orphan, elsewhere

        orphan, elsewhere = run(scenario())
        assert services.approvals[orphan]["status"] == "expired"
        assert services.approvals[elsewhere].get("status") != "expired"
        closed = [e["approval_id"] for e in services.events
                  if e["event"] == "question_closed" and e["status"] == "expired"]
        assert orphan in closed and elsewhere not in closed

    def test_an_answer_after_the_asker_is_gone_is_told_expired(self):
        session, services = build([action(action="finish")])

        async def scenario():
            await session.open()
            await session.deliver_answer("apr_gone", "Work")

        run(scenario())
        assert any(e["event"] == "question_closed"
                   and e["status"] == "expired" for e in services.events)

    def test_the_next_session_carries_the_same_mind(self):
        services = SimSessionServices()
        first, _ = build([
            action(action="say", text="Hello!"),
            action(action="finish"),
        ], services)
        second, _ = build([
            action(action="say", text="Still here."),
            action(action="finish"),
        ], services)

        async def scenario():
            await first.open()
            await first.deliver_user("hi")
            await first.wait_idle()
            await second.open()
            await second.deliver_user("you again?")
            await second.wait_idle()

        run(scenario())
        # One system frame, one continuous transcript across processes.
        state = services.states["chat_1"]
        roles = [m["role"] for m in state["messages"]]
        assert roles.count("system") == 1
        texts = [m["parts"][0]["content"]
                 for m in services.messages["chat_1"]]
        assert texts == ["hi", "Hello!", "you again?", "Still here."]


class TestACycleThatDies:
    def test_the_failure_is_said_and_the_session_lives_on(self):
        """Once, an exception out of the cycle ended the task unread: the
        spinner stopped and the person saw nothing. The failure is said,
        the state persisted every beat stands, and the next message is
        heard as usual."""
        session, services = build([
            action(action="say", text="Back."),
            action(action="finish"),
        ])

        async def scenario():
            await session.open()
            real = session.assistant.run

            async def boom():
                raise RuntimeError("wire cut")

            session.assistant.run = boom
            await session.deliver_user("hello?")
            await session.wait_idle()
            session.assistant.run = real
            await session.deliver_user("still there?")
            await session.wait_idle()

        run(scenario())
        said = [m["parts"][0]["content"] for m in services.messages["chat_1"]
                if m["actor"] == "ai"]
        assert len(said) == 2
        assert "went wrong" in said[0] and "kept" in said[0]
        assert said[1] == "Back."


class TestApprovals:
    def test_a_foreground_approval_runs_after_the_yes(self):
        async def decider(request):
            return True

        services = SimSessionServices(decider=decider)
        services.provider.secrets.update(SYNC_SECRET)
        session, _ = build([
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.sync.push",
                   inputs={"notebook": "work"}),
            action(action="say", text="Pushed."),
            action(action="finish"),
        ], services)

        async def scenario():
            await session.open()
            await session.deliver_user("push my notes")
            await session.wait_idle()

        run(scenario())
        entry = session.assistant.state.trace[-1]
        assert entry["status"] == "success"
        assert len(entry["result"]["digest"]) == 64
        card = next(e for e in services.events
                    if e["event"] == "approval_requested")
        assert card["function"] == "notebook.sync.push"

    def test_a_denied_approval_is_an_honest_error(self):
        async def decider(request):
            return False

        services = SimSessionServices(decider=decider)
        services.provider.secrets.update(SYNC_SECRET)
        session, _ = build([
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.sync.push",
                   inputs={"notebook": "work"}),
            action(action="say", text="The push was not approved."),
            action(action="finish"),
        ], services)

        async def scenario():
            await session.open()
            await session.deliver_user("push")
            await session.wait_idle()

        run(scenario())
        entry = session.assistant.state.trace[-1]
        assert entry["status"] == "error"
        assert "not approved" in entry["result"]["error"]

    def test_an_approval_parks_the_job_and_never_the_mind(self):
        """The model's rule made flesh: while a level-3 job waits on a
        human, the assistant answers an interjection — then the decision
        arrives and the job completes in the same incarnation."""
        services = SimSessionServices()          # approvals HELD open
        services.provider.secrets.update(SYNC_SECRET)
        session, _ = build([
            action(action="open_agent", agent="notebook"),
            action(action="start", function="notebook.sync.push",
                   inputs={"notebook": "work"}),
            action(action="finish"),             # idle; job waits on human
            action(action="say", text="Still waiting on your approval."),
            action(action="finish"),
            action(action="say", text="Approved and pushed."),
            action(action="finish"),
        ], services)

        async def scenario():
            await session.open()
            await session.deliver_user("push in the background")
            approval_id = await approval_open(services)

            job = next(iter(session.assistant.state.jobs.values()))
            assert job.status == "waiting_approval"

            # Interjection while the job waits: absorbed, answered.
            await session.deliver_user("everything ok?")
            while len(services.messages["chat_1"]) < 3:
                await asyncio.sleep(0.02)

            await session.deliver_approval(approval_id, True)
            await session.wait_idle()

        run(scenario())
        job = next(iter(session.assistant.state.jobs.values()))
        assert job.status == "done"
        texts = [m["parts"][0]["content"]
                 for m in services.messages["chat_1"]
                 if m["actor"] == "ai"]
        assert texts[0] == "Still waiting on your approval."
        # The say that accounts for the push carries its verified part.
        assert texts[1] == "Approved and pushed."
        assert verified(services.messages["chat_1"][-1]) == ["Verified: Push Notes"]

    def test_a_waiting_job_survives_its_process_dying(self):
        """The crown: park → crash → hydrate → decide → the job resumes
        through the executor's re-verification gates and the new
        incarnation reports it."""
        services = SimSessionServices()
        services.provider.secrets.update(SYNC_SECRET)
        first, _ = build([
            action(action="open_agent", agent="notebook"),
            action(action="start", function="notebook.sync.push",
                   inputs={"notebook": "work"}),
            action(action="finish"),
        ], services)

        async def crash():
            await first.open()
            await first.deliver_user("push in the background")
            approval_id = await approval_open(services)
            first.abandon()                       # the process dies
            return approval_id

        approval_id = run(crash())
        assert services.states["chat_1"]["jobs"]  # the park was durable

        second, _ = build([
            action(action="say", text="Done — your notes are pushed."),
            action(action="finish"),
        ], services)

        async def revive():
            await second.open()
            await second.deliver_approval(approval_id, True)
            await second.wait_idle()

        run(revive())
        job = next(iter(second.assistant.state.jobs.values()))
        assert job.status == "done"
        assert len(job.result["digest"]) == 64
        final = services.messages["chat_1"][-1]
        # Even across the crash, the resumed job's evidence rides the say.
        assert final["parts"][0]["content"] == "Done — your notes are pushed."
        assert verified(final) == ["Verified: Push Notes"]


class TestAttachments:
    def test_what_the_person_attached_reaches_the_mind_by_ref(self):
        # The upload was persisted as a part and never mentioned to the
        # model, so no agent could be asked to read it. It travels with
        # the words now: name and the ref a function reads it by.
        session, services = build([
            action(action="say", text="I see two files.", final=True),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("compare these", parts=[
                {"type": "file", "resource_ref": "fil_a",
                 "filename": "proposal-a.pdf", "file_type": "application/pdf"},
                {"type": "file", "resource_ref": "fil_b",
                 "filename": "proposal-b.pdf"},
                {"type": "markdown", "content": "ignored"},
            ])
            await session.wait_idle()

        run(scenario())
        turn = next(m["content"] for m in session.assistant.state.messages
                    if m["role"] == "user" and "compare these" in m["content"])
        assert "[attached: proposal-a.pdf (application/pdf) → file_ref fil_a]" in turn
        assert "[attached: proposal-b.pdf → file_ref fil_b]" in turn
        # Durable with the event, so a rehydrated mind reads it too.
        recorded = [e for e in run(services.events_since("chat_1", 0))
                    if e.get("event") == "user_message"][-1]
        assert [a["resource_ref"] for a in recorded["attachments"]] == [
            "fil_a", "fil_b"]


class TestOtherVoices:
    def test_a_wakeup_event_wakes_the_assistant(self):
        """The reminders design's entry point: a schedule fires, the
        assistant is woken with the event, works, and reports."""
        session, services = build([
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.note.find",
                   inputs={"query": "due"}),
            action(action="say", text="Nothing due right now."),
            action(action="finish"),
        ])

        async def scenario():
            await session.open()
            await session.deliver_event(
                {"event": "wakeup", "schedule_id": "sch_1"})
            await session.wait_idle()

        run(scenario())
        assert session.assistant.state.trace[0]["function"] == \
            "notebook.note.find"
        assert services.messages["chat_1"][-1]["parts"][0]["content"] == \
            "Nothing due right now."

    def test_stop_silences_a_working_session(self):
        session, services = build([
            action(action="open_agent", agent="notebook"),
            action(action="start", function="notebook.note.find", inputs={}),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("search")
            # Two script entries only: the stop must prevent any third
            # model call.
            await session.stop()

        run(scenario())
        assert services.states["chat_1"]  # the state was persisted


class TestDurableEvents:
    """Durable before absorbed, absorbed exactly once — the cursor's
    contract (docs/system/assistant.md, Events)."""

    def test_a_hydrated_mind_reads_the_present_roster(self):
        """The frame a mind was saved with is yesterday's: an agent
        installed since must be in front of it when it wakes."""
        services = SimSessionServices()
        first = Session("chat_1", {}, FakeConnector([
            action(action="say", text="Hello!"),
            action(action="finish"),
        ]), services)

        async def yesterday():
            await first.open()
            await first.deliver_user("hi")
            await first.wait_idle()

        run(yesterday())
        assert "(none installed)" in \
            services.states["chat_1"]["messages"][0]["content"]

        second, _ = build([action(action="finish")], services)
        run(second.open())
        frame = second.assistant.state.messages[0]
        assert frame["role"] == "system"
        assert "notebook" in frame["content"]
        assert "(none installed)" not in frame["content"]
        # The transcript beneath the frame is untouched.
        assert [m["role"] for m in second.assistant.state.messages[1:]] == \
            [m["role"] for m in services.states["chat_1"]["messages"][1:]]

    def test_an_event_that_fired_while_down_is_found_on_hydration(self):
        services = SimSessionServices()
        first, _ = build([
            action(action="say", text="Hello!"),
            action(action="finish"),
        ], services)
        second, _ = build([
            action(action="say", text="Reminder!"),
            action(action="finish"),
        ], services)

        async def scenario():
            await first.open()
            await first.deliver_user("hi")
            await first.wait_idle()
            # The clock fires while no mind is advancing.
            await services.record_event(
                "chat_1", {"event": "wakeup", "note": "tick"})
            await second.open()
            await second.wait_idle()

        run(scenario())
        texts = [m["parts"][0]["content"]
                 for m in services.messages["chat_1"]]
        assert texts == ["hi", "Hello!", "Reminder!"]
        transcript = services.states["chat_1"]["messages"]
        assert sum("EVENT wakeup" in m["content"] for m in transcript) == 1
        assert services.states["chat_1"]["cursor"] == 2

    def test_an_absorbed_event_is_never_replayed(self):
        services = SimSessionServices()
        first, _ = build([
            action(action="say", text="Reminder!"),
            action(action="finish"),
        ], services)
        second, _ = build([
            action(action="say", text="Again?!"),
            action(action="finish"),
        ], services)

        async def scenario():
            await first.open()
            await first.deliver_event({"event": "wakeup", "note": "tick"})
            await first.wait_idle()
            await second.open()
            await second.wait_idle()

        run(scenario())
        assert second.connector.calls == []  # nothing woke the second mind
        texts = [m["parts"][0]["content"]
                 for m in services.messages["chat_1"]]
        assert texts == ["Reminder!"]

    def test_a_message_the_beat_never_saw_is_replayed_once(self):
        services = SimSessionServices()
        first, _ = build([
            action(action="say", text="Hello!"),
            action(action="finish"),
        ], services)
        second, _ = build([
            action(action="say", text="Got it."),
            action(action="finish"),
        ], services)

        async def scenario():
            await first.open()
            await first.deliver_user("hi")
            await first.wait_idle()
            # Spoken, recorded — and the process dies before a beat.
            await first.deliver_user("second thing")
            first.abandon()
            await second.open()
            await second.wait_idle()

        run(scenario())
        texts = [m["parts"][0]["content"]
                 for m in services.messages["chat_1"]]
        assert texts == ["hi", "Hello!", "second thing", "Got it."]
        # The person's words only — observations share the user role.
        spoken = [m["content"].split("] ", 1)[-1]
                  for m in services.states["chat_1"]["messages"]
                  if m["role"] == "user"
                  and not m["content"].startswith("OBSERVATION:")]
        assert spoken == ["hi", "second thing"]

    def test_a_fresh_mind_does_not_hear_its_history_twice(self):
        services = SimSessionServices()
        first, _ = build([], services)
        second, _ = build([
            action(action="say", text="Hello!"),
            action(action="finish"),
        ], services)

        async def scenario():
            await first.open()
            # The very first message, and death before any beat: no
            # state was ever persisted.
            await first.deliver_user("hi")
            first.abandon()
            await second.open()
            await second.wait_idle()

        run(scenario())
        # Heard once — and answered, because an unanswered message is
        # unfinished business for a hydrated mind.
        texts = [m["parts"][0]["content"]
                 for m in services.messages["chat_1"]]
        assert texts == ["hi", "Hello!"]
        spoken = [m["content"].split("] ", 1)[-1]
                  for m in services.states["chat_1"]["messages"]
                  if m["role"] == "user"
                  and not m["content"].startswith("OBSERVATION:")]
        assert spoken == ["hi"]
        assert services.states["chat_1"]["cursor"] == 1


class TestOrphanedJobs:
    def test_a_job_running_at_death_settles_honestly_on_hydration(self):
        services = SimSessionServices()
        # The last process persisted this and died: a job it was
        # running, and no task left anywhere to finish it.
        left_behind = AssistantState(
            messages=[
                {"role": "system", "content": "frame"},
                {"role": "user", "content": "find my notes"},
                {"role": "assistant", "content": action(action="finish")},
            ],
            jobs={"job_1": Job("job_1", "notebook", "notebook.note.find",
                               {}, status=RUNNING)},
        )
        services.states["chat_1"] = left_behind.to_dict()
        session, _ = build([
            action(action="say",
                   text="That search died with the last process."),
            action(action="finish"),
        ], services)

        async def scenario():
            await session.open()
            await session.wait_idle()

        run(scenario())
        # Settled as an error, recorded in the trace, never retried.
        saved = services.states["chat_1"]
        assert saved["jobs"]["job_1"]["status"] == "failed"
        assert "died" in saved["jobs"]["job_1"]["result"]["error"]
        assert saved["trace"][-1]["status"] == "error"
        assert session.connector.calls, "the mind heard the job_done"
        # And the mind was woken to say so.
        assert services.messages["chat_1"][-1]["parts"][0]["content"] \
            .startswith("That search died")


class TestSummary:
    """Maintenance: a long transcript folds into the frame once the
    cycle is idle, and the durable record is never touched."""

    def test_a_long_transcript_folds_into_the_frame(self):
        """The fold is by size, between beats as well as at idle, and
        the summary it writes is sectioned. The durable record is never
        touched; the folded mind is persisted and carried by the next
        incarnation."""
        from ai_runtime.chat.summarizer import Summarizer

        services = SimSessionServices()
        session, _ = build([
            action(action="say", text="A"), action(action="finish"),
            action(action="say", text="B"), action(action="finish"),
            action(action="say", text="C"), action(action="finish"),
        ], services)
        folded_summary = ("STANDING INSTRUCTIONS\n(none)\n"
                          "DECISIONS AND FACTS\nthe user asked one and two\n"
                          "DONE\nA and B were said\n"
                          "DECLINED OR FAILED\n(none)\nOPEN THREADS\n(none)")
        # The summary's own model, so folds never eat the mind's script.
        session.summarizer = Summarizer(FakeConnector([folded_summary] * 6))
        session.summarizer.FOLD_ABOVE_CHARS = 300
        session.summarizer.KEEP_RECENT_CHARS = 80
        session.summarizer.KEEP_RECENT_MIN = 2

        async def scenario():
            await session.open()
            for text in ("one", "two", "three"):
                await session.deliver_user(text)
                await session.wait_idle()

        run(scenario())
        state = session.assistant.state
        assert state.summary.startswith("STANDING INSTRUCTIONS")
        assert "A and B were said" in state.messages[0]["content"]   # the frame
        # Folded: fewer messages than the three exchanges made, and the
        # kept tail never starts on an observation.
        assert len(state.messages) < 1 + 3 * 4
        assert not str(state.messages[1]["content"]).startswith("OBSERVATION:")
        # The durable record is whole; only the mind's context shrank.
        texts = [m["parts"][0]["content"]
                 for m in services.messages["chat_1"]]
        assert texts == ["one", "A", "two", "B", "three", "C"]
        # And the folded mind was persisted, summary included.
        assert services.states["chat_1"]["summary"] == state.summary

        # The next incarnation carries it, frame and all.
        second, _ = build([], services)
        run(second.open())
        assert second.assistant.state.summary == state.summary
        assert "A and B were said" in second.assistant.state.messages[0]["content"]

    def test_a_message_arriving_during_maintenance_is_not_stranded(self):
        # The cycle is idle and maintenance is awaiting the summary
        # model when the person speaks. The run task is alive, so the
        # pump starts no other — the running one must come back for it.
        session, services = build([
            action(action="say", text="A"), action(action="finish"),
            action(action="say", text="B"), action(action="finish"),
        ])
        entered, release = asyncio.Event(), asyncio.Event()

        class SlowMaintenance:
            async def maintain(self, assistant, force=False):
                entered.set()
                await release.wait()
                return False

        session.summarizer = SlowMaintenance()

        async def scenario():
            await session.open()
            await session.deliver_user("one")
            await entered.wait()              # in maintenance now
            await session.deliver_user("two")  # arrives meanwhile
            release.set()
            await session.wait_idle()
            assert session.assistant.inbox.empty()

        run(scenario())
        texts = [m["parts"][0]["content"]
                 for m in services.messages["chat_1"] if m["actor"] == "ai"]
        assert texts == ["A", "B"]

    def test_a_failed_fold_changes_nothing(self):
        session, services = build([
            action(action="say", text="A"), action(action="finish"),
            action(action="say", text="B"), action(action="finish"),
            # nothing left for the summarizer: its call fails
        ])
        session.summarizer.FOLD_ABOVE = 4
        session.summarizer.KEEP_RECENT = 2

        async def scenario():
            await session.open()
            for text in ("one", "two"):
                await session.deliver_user(text)
                await session.wait_idle()

        run(scenario())
        state = session.assistant.state
        assert state.summary == ""
        # Two exchanges of four: turn, say, its observation, finish.
        assert len(state.messages) == 1 + 8
        assert [m["actor"] for m in services.messages["chat_1"]] == [
            "user", "ai", "user", "ai"]


class TestTheClockAction:
    """The assistant's hand on the clock: schedule and unschedule."""

    def clocked(self, script, services=None, now=1000.0):
        from ai_runtime.chat.scheduler import ScheduleRunner, Scheduler
        from ai_runtime.execution.executor import FunctionExecutor
        from sim.schedules import MemoryScheduleStore

        services = services or SimSessionServices()
        holder = {}

        async def wake(chat_id, event):
            await holder["session"].deliver_event(event)

        agents, errors = load_agents(AGENTS_DIR)
        assert errors == {}
        scheduler = Scheduler(
            MemoryScheduleStore(),
            ScheduleRunner(agents, FunctionExecutor(
                provider=services.provider), wake),
            clock=lambda: now,
        )
        session = Session("chat_1", agents, FakeConnector(script), services,
                          clock=scheduler)
        holder["session"] = session
        return session, scheduler, services

    def test_the_assistant_sleeps_and_is_woken_to_carry_on(self):
        """Work that waits on something outside: the assistant pauses,
        goes idle at once, and is woken with its own reason. The sleep
        is the clock's and not a schedule of the person's — nothing is
        announced, and it is gone once it has fired."""
        session, scheduler, services = self.clocked([
            action(action="say", text="The export is being prepared; I "
                                      "will look again in ten minutes."),
            action(action="sleep", seconds=600,
                   why="check whether the export is ready"),
            action(action="say", text="The export is ready.", final=True),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("export my notes")
            await session.wait_idle()
            [row] = scheduler.schedules
            assert (row.sleep, row.mode, row.next_run_at) == (
                True, "wake", 1000.0 + 600)
            assert row.to_dict()["sleep"] is True
            assert not any(e["event"] == "schedule_set"
                           for e in services.events)
            # The audience is told, live and on arriving later.
            [told] = [e for e in services.events if e["event"] == "sleeping"]
            assert (told["until"], told["why"]) == (
                1000.0 + 600, "check whether the export is ready")
            assert session.sleeping() == {
                "until": 1000.0 + 600,
                "why": "check whether the export is ready"}
            # Asleep is idle: the model was not asked again.
            asleep = len(session.assistant.connector.calls)

            await scheduler.tick(now=1000.0 + 601)
            await session.wait_idle()
            return asleep

        asleep = run(scenario())
        assert asleep == 2
        texts = [m["parts"][0]["content"]
                 for m in services.messages["chat_1"] if m["actor"] == "ai"]
        assert texts[-1] == "The export is ready."
        woken = [m["content"] for m in session.assistant.state.messages
                 if "EVENT wakeup" in str(m.get("content"))]
        assert '"slept": true' in woken[0]
        assert "check whether the export is ready" in woken[0]
        assert scheduler.schedules == []          # gone once it has fired
        assert session.sleeping() is None

    def test_the_person_is_heard_while_the_assistant_sleeps(self):
        """A message during a sleep is answered at once, as always; the
        sleep is left in place and still wakes the chat later."""
        session, scheduler, services = self.clocked([
            action(action="sleep", seconds=600, why="look again"),
            action(action="say", text="Still waiting on it.", final=True),
            action(action="say", text="Done now.", final=True),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("start the export")
            await session.wait_idle()
            await session.deliver_user("how is it going?")
            await session.wait_idle()
            answered = [m["parts"][0]["content"]
                        for m in services.messages["chat_1"]
                        if m["actor"] == "ai"]
            still = [s.sleep for s in scheduler.schedules]
            await scheduler.tick(now=1000.0 + 601)
            await session.wait_idle()
            return answered, still

        answered, still = run(scenario())
        assert answered == ["Still waiting on it."]
        assert still == [True]
        assert scheduler.schedules == []

    def test_a_sleep_is_bounded_single_and_ended_by_a_stop(self):
        from ai_runtime.chat.scheduler import ChatClock

        session, scheduler, _ = self.clocked([])

        async def emit(event):
            pass

        clock = ChatClock(scheduler, "chat_1", {}, emit)

        async def scenario():
            too_long = await clock.sleep(ChatClock.MAX_SLEEP_SECONDS + 1)
            nonsense = await clock.sleep("soon")
            await clock.sleep(60, "first")
            second = await clock.sleep(120, "second")
            rows = [(s.note, s.next_run_at) for s in scheduler.schedules]
            # A stop ends it.
            await session.open()
            session.ask_to_stop()
            await session.wait_idle()
            await asyncio.sleep(0.05)
            return too_long, nonsense, second, rows

        too_long, nonsense, second, rows = run(scenario())
        assert "24 hours" in too_long["error"] and "error" in nonsense
        assert second["seconds"] == 120
        assert rows == [("second", 1000.0 + 120)]       # one at a time
        assert scheduler.schedules == []

    def test_a_reminder_is_set_announced_and_fires(self):
        session, scheduler, services = self.clocked([
            action(action="schedule", note="Pay rent",
                   delay_seconds=3600),
            action(action="say", text="I'll remind you in an hour."),
            action(action="finish"),
            action(action="say", text="Reminder: pay rent."),
            action(action="finish"),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("remind me to pay rent in an hour")
            await session.wait_idle()
            row = scheduler.schedules[0]
            assert (row.chat_id, row.mode, row.note) == (
                "chat_1", "wake", "Pay rent")
            assert row.next_run_at == 1000.0 + 3600
            assert row.every_seconds is None
            # The user saw it set, and the mind read its id back.
            assert any(e["event"] == "schedule_set" for e in services.events)
            # ... schedule observation, say, its observation, finish.
            assert row.schedule_id in session.assistant.state.messages[
                -4]["content"]

            # An hour later: the clock wakes the mind with the note.
            await scheduler.tick(now=1000.0 + 3601)
            await session.wait_idle()

        run(scenario())
        texts = [m["parts"][0]["content"]
                 for m in services.messages["chat_1"] if m["actor"] == "ai"]
        assert texts == ["I'll remind you in an hour.",
                         "Reminder: pay rent."]

    def test_the_manifests_word_gates_unattended_functions(self):
        session, scheduler, _ = self.clocked([
            action(action="schedule", function="notebook.note.save",
                   inputs={"notebook": "x", "title": "sneak"},
                   every_seconds=300),
            action(action="schedule", function="notebook.note.find",
                   inputs={}, wake_field="notes", every_seconds=300),
            action(action="finish"),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("check my notes every 5 minutes")
            await session.wait_idle()

        run(scenario())
        observations = [m["content"] for m in session.assistant.state.messages
                        if m["content"].startswith("OBSERVATION")]
        assert "not schedulable" in observations[0]
        assert [s.function for s in scheduler.schedules] == [
            "notebook.note.find"]
        assert scheduler.schedules[0].next_run_at == 1300.0

    def test_when_must_be_said_and_a_cadence_has_a_floor(self):
        session, scheduler, _ = self.clocked([
            action(action="schedule", note="no when"),
            action(action="schedule", note="too fast", every_seconds=5),
            action(action="schedule", note="at", at="2030-01-01T09:00:00"),
            action(action="finish"),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("schedule things")
            await session.wait_idle()

        run(scenario())
        observations = [m["content"] for m in session.assistant.state.messages
                        if m["content"].startswith("OBSERVATION")]
        assert "Say when" in observations[0]
        assert "at least 60" in observations[1]
        assert [s.note for s in scheduler.schedules] == ["at"]

    def test_a_chat_removes_only_its_own_schedules(self):
        from ai_runtime.chat.scheduler import Schedule

        session, scheduler, services = self.clocked([
            action(action="unschedule", schedule_id="sch_theirs"),
            action(action="unschedule", schedule_id="sch_mine"),
            action(action="finish"),
        ])

        async def scenario():
            await scheduler.add(Schedule("chat_2", "wake", note="theirs",
                                         schedule_id="sch_theirs",
                                         next_run_at=1.0))
            await scheduler.add(Schedule("chat_1", "wake", note="mine",
                                         schedule_id="sch_mine",
                                         next_run_at=1.0))
            await session.open()
            await session.deliver_user("clear my reminder")
            await session.wait_idle()

        run(scenario())
        assert [s.schedule_id for s in scheduler.schedules] == ["sch_theirs"]
        assert any(e["event"] == "schedule_removed" for e in services.events)

    def test_without_a_clock_the_action_is_honest(self):
        session, services = build([
            action(action="schedule", note="x", delay_seconds=60),
            action(action="finish"),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("remind me")
            await session.wait_idle()

        run(scenario())
        observations = [m["content"] for m in session.assistant.state.messages
                        if m["content"].startswith("OBSERVATION")]
        assert "No clock" in observations[0]

    def test_the_mind_knows_when_things_happen(self):
        from datetime import datetime

        now = 1_800_000_000.0
        session, scheduler, _ = self.clocked([
            action(action="schedule", note="ping", delay_seconds=60),
            action(action="finish"),
            action(action="finish"),
        ], now=now)
        stamp = datetime.fromtimestamp(now).strftime("%a %Y-%m-%d %H:%M")

        async def scenario():
            await session.open()
            await session.deliver_user("when is it?")
            await session.wait_idle()
            await scheduler.tick(now=now + 61)
            await session.wait_idle()

        run(scenario())
        transcript = [m["content"] for m in session.assistant.state.messages]
        assert f"[{stamp}] when is it?" in transcript
        assert any(t.startswith(f"EVENT wakeup at {stamp}")
                   for t in transcript)


class TestTheClockPersists:
    """What the clock writes, and when: after each firing, not once
    after the tick — and again on the next tick when a write failed."""

    @staticmethod
    def row(schedule_id):
        return {"schedule_id": schedule_id, "chat_id": "chat_1",
                "mode": "wake", "note": "n", "every_seconds": 60,
                "next_run_at": 1.0, "enabled": True}

    @staticmethod
    def scheduler(store):
        from ai_runtime.chat.scheduler import Scheduler

        fired = []

        class Runner:
            async def fire(self, schedule):
                fired.append(schedule.schedule_id)
                return {"status": "ok"}

        return Scheduler(store, Runner(), clock=lambda: 1000.0), fired

    def test_each_firing_is_written_before_the_next_fires(self):
        from sim.schedules import MemoryScheduleStore

        class CountingStore(MemoryScheduleStore):
            def __init__(self):
                super().__init__()
                self.written = []

            async def ran(self, row):
                self.written.append(row["schedule_id"])
                return await super().ran(row)

        store = CountingStore()
        store.rows = [self.row("a"), self.row("b")]
        scheduler, fired = self.scheduler(store)
        scheduler.adopt(store.rows)
        run(scheduler.tick())
        assert fired == ["a", "b"]
        # One row at a time, each its own: nothing else is rewritten.
        assert store.written == ["a", "b"]
        assert all(r["next_run_at"] > 1000.0 for r in store.rows)

    def test_a_write_that_fails_is_made_again_on_the_next_tick(self):
        from sim.schedules import MemoryScheduleStore

        class FlakyStore(MemoryScheduleStore):
            def __init__(self):
                super().__init__()
                self.failures_left = 1
                self.written = 0

            async def ran(self, row):
                if self.failures_left:
                    self.failures_left -= 1
                    raise RuntimeError("disk full")
                self.written += 1
                return await super().ran(row)

        store = FlakyStore()
        store.rows = [self.row("a")]
        scheduler, fired = self.scheduler(store)
        scheduler.adopt(store.rows)
        # Fires; the write fails — remembered, not swallowed.
        run(scheduler.tick())
        assert fired == ["a"] and store.written == 0
        # Nothing due: the owed write happens before anything else.
        run(scheduler.tick())
        assert fired == ["a"] and store.written == 1
        assert store.rows[0]["next_run_at"] > 1000.0

    def test_a_row_deleted_while_it_fired_leaves_the_clock(self):
        """The person deleted it on the page as it ran: the store says
        it is gone, and the clock does not keep what nothing keeps."""
        from sim.schedules import MemoryScheduleStore

        store = MemoryScheduleStore()
        scheduler, fired = self.scheduler(store)
        scheduler.adopt([self.row("a")])      # the store holds no such row
        run(scheduler.tick())
        assert fired == ["a"] and scheduler.schedules == []
        assert store.rows == []

    def test_a_row_paused_while_it_fired_stays_paused_and_moves_on(self):
        """The person paused it as it ran, and the clock re-read the
        chat's rows. The run is the new row's: it is not switched back
        on, and it does not fire again for the same moment."""
        from ai_runtime.chat.scheduler import Scheduler
        from sim.schedules import MemoryScheduleStore

        store = MemoryScheduleStore()
        store.rows = [self.row("a")]
        fired = []

        class Runner:
            async def fire(runner, schedule):
                fired.append(schedule.schedule_id)
                scheduler.replace_for(
                    "chat_1", [{**self.row("a"), "enabled": False}])
                return {"status": "ok"}

        scheduler = Scheduler(store, Runner(), clock=lambda: 1000.0)
        scheduler.adopt(store.rows)
        run(scheduler.tick())
        run(scheduler.tick())
        assert fired == ["a"]
        assert store.rows[0]["enabled"] is False
        assert store.rows[0]["next_run_at"] > 1000.0
        assert len(store.rows[0]["runs"]) == 1

    def test_a_chat_that_is_gone_takes_its_rows_off_the_clock(self):
        """A deleted chat, or a key no longer accepted: its rows would
        fire and fail every time they came round."""
        from sim.schedules import MemoryScheduleStore

        class Gone(RuntimeError):
            status = 404

        class Store(MemoryScheduleStore):
            async def ran(self, row):
                if row["chat_id"] == "chat_1":
                    raise Gone("chat not found")
                return await super().ran(row)

        store = Store()
        other = {**self.row("z"), "chat_id": "chat_2"}
        store.rows = [self.row("a"), self.row("b"), other]
        scheduler, fired = self.scheduler(store)
        scheduler.adopt(store.rows)
        run(scheduler.tick())
        # b went with a (its own fire was already under way; its row is
        # no longer there to write); the other chat's row is untouched.
        assert fired == ["a", "b", "z"]
        assert [s.schedule_id for s in scheduler.schedules] == ["z"]

    def test_a_slow_fire_holds_up_no_other_row(self):
        """Each fire is its own task. One that takes its time — a slow
        site, a question waiting on the person — does not keep the next
        row, or the next tick, waiting; and it is not fired again while
        it is still under way."""
        from ai_runtime.chat.scheduler import Scheduler
        from sim.schedules import MemoryScheduleStore

        store = MemoryScheduleStore()
        store.rows = [self.row("slow"), self.row("quick")]
        fired = []

        async def scenario():
            release = asyncio.Event()

            class Runner:
                async def fire(self, schedule):
                    fired.append(schedule.schedule_id)
                    if schedule.schedule_id == "slow":
                        await release.wait()
                    return {"status": "ok"}

            scheduler = Scheduler(store, Runner(), clock=lambda: 1000.0)
            scheduler.adopt(store.rows)
            await scheduler.tick(wait=False)
            await asyncio.sleep(0.05)
            quick_done = store.rows[1]["next_run_at"] > 1000.0
            slow_waiting = store.rows[0]["next_run_at"] == 1.0
            # Round again while the slow one is still out: not refired.
            await scheduler.tick(wait=False)
            await asyncio.sleep(0.05)
            again = list(fired)
            release.set()
            await scheduler.tick()
            return quick_done, slow_waiting, again

        quick_done, slow_waiting, again = run(scenario())
        assert quick_done and slow_waiting
        assert again == ["slow", "quick"]
        assert store.rows[0]["next_run_at"] > 1000.0

    def test_a_kill_ends_the_chats_fires_and_their_rows_move_on(self):
        from ai_runtime.chat.scheduler import Scheduler
        from sim.schedules import MemoryScheduleStore

        store = MemoryScheduleStore()
        store.rows = [self.row("mine"), {**self.row("theirs"), "chat_id": "chat_2"}]

        async def scenario():
            class Runner:
                async def fire(self, schedule):
                    await asyncio.Event().wait()   # never, by itself

            scheduler = Scheduler(store, Runner(), clock=lambda: 1000.0)
            scheduler.adopt(store.rows)
            await scheduler.tick(wait=False)
            await asyncio.sleep(0.05)
            ended = scheduler.cancel_chat("chat_1")
            await asyncio.sleep(0.05)
            still = sorted(scheduler._firing)
            await scheduler.stop()
            return ended, still

        ended, still = run(scenario())
        assert ended == 1 and still == ["theirs"]
        mine, theirs = store.rows
        # Said on the row, which is not due again at the next tick.
        assert mine["runs"][-1]["error"] == "stopped by the person"
        assert mine["next_run_at"] > 1000.0
        # A fire the process's end cut short is still due.
        assert theirs["next_run_at"] == 1.0 and not theirs.get("runs")

    def test_a_schedule_the_store_refuses_is_not_on_the_clock(self):
        from ai_runtime.chat.scheduler import ChatClock
        from sim.schedules import MemoryScheduleStore

        class Full(MemoryScheduleStore):
            async def add(self, row):
                raise RuntimeError("A chat may hold at most 50 schedules.")

        scheduler, _ = self.scheduler(Full())
        said = []

        async def emit(event):
            said.append(event)

        clock = ChatClock(scheduler, "chat_1", {}, emit)
        answer = run(clock.schedule({"note": "stretch", "delay_seconds": 60}))
        assert "at most 50" in answer["error"]
        assert scheduler.schedules == [] and said == []


class TestTheSocketKeepsHearing:
    def test_a_stop_is_asked_and_not_waited_for(self):
        """A plain stop is honored between beats, and the beat under
        way may be a long one. The socket's loop does not wait it out:
        what comes next — the kill switch — must still be heard."""
        asked = []

        class Busy:
            def ask_to_stop(self):
                asked.append("stop")

            async def stop(self):
                raise AssertionError("the socket's loop waited for the stop")

        async def scenario():
            from ai_runtime.server.host import SessionHost

            host = SessionHost(SimSessionServices(), {})
            host.sessions["chat_1"] = Busy()
            await host.handle("chat_1", {"event": "stop"})

        run(scenario())
        assert asked == ["stop"]


class TestCompaction:
    def test_maintenance_compacts_and_persists_the_trace(self):
        session, services = build([
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.note.find",
                   inputs={}),
            action(action="invoke", function="notebook.note.find",
                   inputs={}),
            action(action="invoke", function="notebook.note.find",
                   inputs={}),
            action(action="say", text="Nothing found, three times."),
            action(action="finish"),
        ])

        async def scenario():
            await session.open()
            session.assistant.state.TRACE_KEEP = 1
            await session.deliver_user("look thrice")
            await session.wait_idle()

        run(scenario())
        # Three entries were presented by the say; maintenance kept one.
        assert len(session.assistant.state.trace) == 1
        assert len(services.states["chat_1"]["trace"]) == 1
        assert services.states["chat_1"]["evidence_cursor"] == 1


class TestForegroundParks:
    """A park in a foreground invoke has no job — it is recorded on the
    state instead, durable before its card, so it is findable by a
    late audience and survives the process that parked it."""

    def test_a_foreground_park_survives_its_process_dying(self):
        services = SimSessionServices()          # approvals HELD open
        services.provider.secrets.update(SYNC_SECRET)
        first, _ = build([
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.sync.push",
                   inputs={"notebook": "work"}),
        ], services)
        second, _ = build([
            action(action="say", text="Pushed, after all."),
            action(action="finish"),
        ], services)

        async def scenario():
            await first.open()
            await first.deliver_user("push my notes")
            approval_id = await approval_open(services)
            parked = services.states["chat_1"]["parked"]
            assert parked["approval_id"] == approval_id
            assert parked["function"] == "notebook.sync.push"
            first.abandon()                       # the process dies

            await second.open()
            assert second.holds_approval(approval_id)
            assert second.pending_cards()[0]["function"] == \
                "notebook.sync.push"
            await second.deliver_approval(approval_id, True)
            await second.wait_idle()

        run(scenario())
        entry = second.assistant.state.trace[-1]
        assert entry["function"] == "notebook.sync.push"
        assert entry["status"] == "success"
        assert second.assistant.state.parked is None
        assert services.states["chat_1"]["parked"] is None
        texts = [m["parts"][0]["content"]
                 for m in services.messages["chat_1"] if m["actor"] == "ai"]
        assert texts[0].startswith("Pushed, after all.")

    def test_a_resumed_park_must_hash_to_what_the_card_recorded(self):
        # The decision carries the hash the card was opened with; the
        # hydrated inputs must hash to it. A hash for other inputs
        # blocks the run — and the card's real hash lets it through.
        services = SimSessionServices()
        services.provider.secrets.update(SYNC_SECRET)
        first, _ = build([
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.sync.push",
                   inputs={"notebook": "work"}),
        ], services)
        second, _ = build([
            action(action="say", text="Blocked."),
            action(action="finish"),
        ], services)
        third, _ = build([
            action(action="say", text="Pushed."),
            action(action="finish"),
        ], services)

        async def scenario():
            await first.open()
            await first.deliver_user("push my notes")
            approval_id = await approval_open(services)
            recorded = services.approvals[approval_id]["request"]["action_hash"]
            assert recorded
            first.abandon()

            await second.open()
            await second.deliver_approval(approval_id, True,
                                          action_hash="0" * 64)
            await second.wait_idle()
            entry = second.assistant.state.trace[-1]
            assert entry["status"] == "error"
            assert "does not match its approval" in entry["result"]["error"]

            # A refused resume settles the park; open the same card
            # again on a fresh mind with the hash it was opened with.
            services.states["chat_1"]["parked"] = {
                "approval_id": approval_id,
                "agent_id": "notebook", "function": "notebook.sync.push",
                "inputs": {"notebook": "work"}, "permission_level": 2,
            }
            await third.open()
            await third.deliver_approval(approval_id, True,
                                         action_hash=recorded)
            await third.wait_idle()
            assert third.assistant.state.trace[-1]["status"] == "success"

        run(scenario())

    def test_the_persons_words_reach_the_call_showing_a_screen(self):
        """The person speaks while an agent drives a screen they watch.
        The mind hears it as any interjection; the running call hears it
        too, as a `say` on its screen, so the browser can be steered
        mid-run rather than after the answer."""
        session, services = build([
            action(action="open_agent", agent="screen"),
            action(action="invoke", function="screen.show.run",
                   inputs={"wait_seconds": 5}),
            action(action="say", text="Done.", final=True),
            action(action="finish"),
            action(action="finish"),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("show me your screen")
            deadline = asyncio.get_running_loop().time() + 20
            while not session.screens:
                assert asyncio.get_running_loop().time() < deadline, "no screen"
                await asyncio.sleep(0.05)
            await session.deliver_user("go left, not right", parts=[
                {"type": "file", "resource_ref": "fil_cv", "filename": "cv.pdf",
                 "file_type": "application/pdf"}])
            await session.wait_idle()

        run(scenario())
        transcript = [str(m.get("content")) for m in session.assistant.state.messages]
        heard = [c for c in transcript if "go left, not right" in c and '"said"' in c]
        assert heard, "the call's result names what the person said"
        assert all("attached a file: cv.pdf (file_ref fil_cv)" in c for c in heard), \
            "a file attached mid-run is named to the call with its ref"
        assert all('"inputs": []' in c for c in heard), "words are not inputs"
        # The call knew which conversation it ran in: the chat's key.
        assert all('"conversation": "chat_1"' in c for c in heard)

    def test_the_person_opens_a_screen_before_asking_anything(self):
        """The page asks for a browser; the roster's watch function is
        called directly, no model involved, streams until the person
        closes the panel, and a chat without such an agent is told."""
        session, services = build([action(action="finish")])

        async def scenario():
            await session.open()
            assert await session.open_screen()
            deadline = asyncio.get_running_loop().time() + 20
            while not any(e["event"] == "screen_frame" for e in services.relayed):
                assert asyncio.get_running_loop().time() < deadline, "no frame"
                await asyncio.sleep(0.05)
            frame = next(e for e in services.relayed if e["event"] == "screen_frame")
            assert frame["source"]["function"] == "screen.show.watch"
            # Asked again while it shows: the same watch, not a second.
            watching = session.watching
            assert await session.open_screen() and session.watching is watching
            await session.deliver_screen_input(frame["call_id"], [
                {"type": "control", "action": "close"}])
            await asyncio.wait_for(session.watching, 10)
            # Closing the browser itself: the same function, told quit.
            assert await session.open_screen("quit")
            await asyncio.sleep(0.5)
            # A chat whose roster cannot show a screen is told so.
            session.roster.pop("screen")
            return await session.open_screen()

        opened_again = run(scenario())
        assert opened_again is False
        assert any(e["event"] == "screen_closed" for e in services.relayed)
        assert any(e["event"] == "screen_unavailable" for e in services.relayed)
        # No model spoke: the watch is not a turn.
        assert not [m for m in session.assistant.state.messages if m["role"] == "assistant"]
        assert any("show.watch" in str(e) and "quit" in str(e)
                   for e in services.audit), "the quit reached the function"

    def test_the_chat_is_named_after_the_first_answer(self):
        """One small call to the chat's model, after the first answer,
        names the chat from its content; the page hears it. A helper
        never names anything."""
        session, services = build([
            action(action="say", text="Five tasks are open.", final=True),
            "Open tasks this week",
        ])

        session.connector.names_chats = True

        async def scenario():
            await session.open()
            await session.deliver_user("what is open?")
            await session.wait_idle()

        run(scenario())
        assert services.titles["chat_1"] == "Open tasks this week"
        assert any(e["event"] == "chat_titled" and e["title"] == "Open tasks this week"
                   for e in services.relayed)
        # A name the person typed stands: the platform refuses the model's.
        services.titles_by_person["chat_1"] = True
        session.named_at = 0
        run(session._name_chat())
        assert services.titles["chat_1"] == "Open tasks this week"

    def test_a_denied_hydrated_park_is_an_honest_observation(self):
        services = SimSessionServices()
        services.provider.secrets.update(SYNC_SECRET)
        first, _ = build([
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.sync.push",
                   inputs={"notebook": "work"}),
        ], services)
        second, _ = build([
            action(action="say", text="Understood — not pushed."),
            action(action="finish"),
        ], services)

        async def scenario():
            await first.open()
            await first.deliver_user("push")
            approval_id = await approval_open(services)
            first.abandon()
            await second.open()
            await second.deliver_approval(approval_id, False)
            await second.wait_idle()

        run(scenario())
        entry = second.assistant.state.trace[-1]
        assert entry["status"] == "error"
        assert "not approved" in entry["result"]["error"]
        assert services.provider.data == {}   # nothing ran


class TestScreens:
    """A screen an agent shows (call.screen): frames reach the audience
    and nothing else, and the person's hand reaches the call showing
    it — both directions over the worker pipe, proven without a
    browser (fixtures/agents/screen)."""

    def test_frames_reach_the_audience_and_input_reaches_the_call(self):
        session, services = build([
            action(action="open_agent", agent="screen"),
            action(action="invoke", function="screen.show.run",
                   inputs={"wait_seconds": 5}),
            action(action="say", text="Done.", final=True),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("show me your screen")
            deadline = asyncio.get_running_loop().time() + 20
            while not any(e["event"] == "screen_frame" for e in services.relayed):
                assert asyncio.get_running_loop().time() < deadline, "no frame"
                await asyncio.sleep(0.05)
            frame = next(e for e in services.relayed if e["event"] == "screen_frame")
            assert frame["source"]["agent"] == "screen"
            assert frame["image_base64"] and frame["width"] == 1 and frame["taken"] is False
            # What is open behind the picture travels with it, within
            # the contract's bounds, and the frame fits the vocabulary.
            assert frame["tabs"] == [
                {"index": 1, "title": "Inbox", "address": "https://mail.example/", "active": True},
                {"index": 2, "title": "T" * 200, "address": "", "active": False}]
            assert event_error({k: v for k, v in frame.items()}) is None
            assert session.screens, "the session knows a screen is showing"
            delivered = await session.deliver_screen_input(frame["call_id"], [
                {"type": "control", "action": "take"},
                {"type": "mouse", "action": "down", "x": 1, "y": 1, "button": "left"},
                {"type": "key", "action": "down", "key": "a", "text": "a"},
                {"type": "tab", "action": "close", "index": 2},
            ])
            assert delivered
            assert not await session.deliver_screen_input("c_nowhere", [
                {"type": "control", "action": "take"}])
            await session.wait_idle()

        run(scenario())
        frames = [e for e in services.relayed if e["event"] == "screen_frame"]
        assert [f["frame"] for f in frames] == [1, 2, 3]
        assert all("seq" not in f for f in frames), "a picture carries no record number"
        assert any(e["event"] == "screen_closed" for e in services.relayed)
        # Delivered, never recorded.
        assert not any(e.get("event") in ("screen_frame", "screen_closed")
                       for e in services.events)
        assert session.screens == {}
        observation = next(
            m["content"] for m in session.assistant.state.messages
            if m["content"].startswith("OBSERVATION") and '"inputs"' in m["content"])
        assert '"take"' in observation and '"key": "a"' in observation
        assert '{"type": "tab", "action": "close", "index": 2}' in observation
        assert '"taken": true' in observation
        assert '"frames": 3' in observation


class TestWhichSkills:
    """The person's narrowing of a chat's skills (enabled_skills)
    reaches the mind: the frame lists those and no other."""

    @staticmethod
    def _services():
        services = SimSessionServices()
        services.skills_rows = [
            {"ref": "sk_1", "title": "Weekly report", "summary": "how the report is built"},
            {"ref": "sk_2", "title": "Expense rules", "summary": "what may be claimed"},
            {"ref": "sk_3", "title": "Style guide", "summary": "how we write"},
        ]
        return services

    def test_a_narrowed_chat_is_shown_its_own_skills_only(self):
        session, _ = build([action(action="finish")], services=self._services(),
                           skills=["sk_2", "sk_gone"])
        run(session.open())
        frame = session.assistant._system_prompt()
        assert "sk_2: Expense rules" in frame
        assert "sk_1:" not in frame and "sk_3:" not in frame
        assert "more skill(s)" not in frame

    def test_no_narrowing_means_every_visible_skill(self):
        session, _ = build([action(action="finish")], services=self._services())
        run(session.open())
        frame = session.assistant._system_prompt()
        assert all(f"sk_{n}:" in frame for n in (1, 2, 3))

    def test_a_choice_made_since_the_build_cuts_the_list_at_the_next_turn(self):
        session, _ = build([action(action="finish")], services=self._services())
        run(session.open())
        session.adopt(session.roster, session.chat_level, skills=["sk_3"])
        frame = session.assistant._system_prompt()
        assert "sk_3: Style guide" in frame
        assert "sk_1:" not in frame and "sk_2:" not in frame


class TestTheKillSwitch:
    """Stop everything: the run and its jobs end where they stand, the
    cards nobody will answer are expired, the state says cancelled, and
    the session is forgotten so the next message starts quiet."""

    def test_a_working_session_is_killed_mid_job_and_its_cards_expired(self):
        session, services = build([
            action(action="open_agent", agent="notebook"),
            action(action="start", function="notebook.note.find", inputs={}),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("search")
            # A card the person will never answer now.
            approval_id = await services.open_approval("chat_1", {
                "function": "notebook.note.find", "kind": "approval"})
            counts = await session.kill()
            return approval_id, counts

        approval_id, counts = run(scenario())
        assert session.idle
        assert counts["cards"] >= 1
        assert services.approvals[approval_id]["status"] == "expired"
        assert all(job.status == "cancelled" for job in session.assistant.state.jobs.values())
        assert not session.assistant.state.active_jobs()
        events = [e["event"] for e in [e for e in services.events if e.get("chat_id") == "chat_1"]]
        assert "stopped" in events
        # The persisted mind carries the cancellation: a rebuild is quiet.
        saved = services.states["chat_1"]
        assert all(job["status"] == "cancelled" for job in saved["jobs"].values())

    def test_a_forced_stop_frame_kills_and_forgets_the_session(self):
        from types import SimpleNamespace

        from ai_runtime.server.host import SessionHost
        from ai_runtime.tests.fixture_agents import load_agents

        agents, errors = load_agents(AGENTS_DIR)
        assert errors == {}
        services = SimSessionServices()
        services.contracts["chat_1"] = {"llm": {"provider": "fake", "responses": [
            action(action="open_agent", agent="notebook"),
            action(action="start", function="notebook.note.find", inputs={}),
        ]}}
        host = SessionHost(services, SimpleNamespace(loaded=lambda: agents))

        async def scenario():
            await host.handle("chat_1", {"event": "user_message", "text": "search"})
            assert "chat_1" in host.sessions
            await host.handle("chat_1", {"event": "stop", "force": True})
            deadline = asyncio.get_running_loop().time() + 20
            while "chat_1" in host.sessions or "stopped" not in [
                    e["event"] for e in services.events if e.get("chat_id") == "chat_1"]:
                assert asyncio.get_running_loop().time() < deadline, "not stopped"
                await asyncio.sleep(0.05)

        run(scenario())
        assert "chat_1" not in host.sessions
        assert "stopped" in [e["event"] for e in [e for e in services.events if e.get("chat_id") == "chat_1"]]


class TestTheClockRefusesTwins:
    """The same thing at the same cadence is not put on the clock twice;
    the error names the row that already is."""

    def test_a_second_identical_schedule_is_refused_with_the_first_named(self):
        session, scheduler, services = TestTheClockAction().clocked([
            action(action="schedule", note="weekly review", cron="44 10 * * 1"),
            action(action="schedule", note="weekly review", cron="44 10 * * 1"),
            action(action="schedule", note="weekly review", cron="0 9 * * 1"),
            action(action="finish"),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("set it")
            await session.wait_idle()

        run(scenario())
        rows = [s for s in scheduler.schedules if s.chat_id == "chat_1"]
        assert len(rows) == 2, "the twin was refused, the different cadence was not"
        observations = [m["content"] for m in session.assistant.state.messages
                        if m.get("role") == "user" and m["content"].startswith("OBSERVATION")]
        assert any("already on the clock as " + rows[0].schedule_id in o for o in observations)
