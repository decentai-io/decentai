"""The assistant's cycle (docs/system/assistant.md), against real agents.

Scripted model, real everything else: real executor, real workers over
the real wire, real evidence. What these tests pin is what the cycle
ENFORCES — open-before-invoke, bounces, the valve, jobs, interjection,
stop — not what any model happens to emit.
"""

import asyncio
import json
from pathlib import Path

from ai_runtime.execution.executor import FunctionExecutor
from ai_runtime.llms import FakeConnector
from ai_runtime.reasoning import Assistant, AssistantState
from ai_runtime.tests.fixture_agents import load_agents
from sim.resources import InMemoryResourceProvider

AGENTS_DIR = Path(__file__).resolve().parent / "fixtures" / "agents"


def run(awaitable):
    return asyncio.run(awaitable)


def action(**kwargs):
    return json.dumps(kwargs)


class Harness:
    """One assistant with collecting sinks, real agents, real executor."""

    def __init__(self, script, provider=None, **kwargs):
        self.agents, errors = load_agents(AGENTS_DIR)
        assert errors == {}
        self.provider = provider or InMemoryResourceProvider()
        self.connector = FakeConnector(script)
        self.said = []
        self.saved_states = []
        self.state = kwargs.pop("state", None) or AssistantState()

        async def say_sink(text, parts):
            self.said.append({"text": text, "parts": parts})

        async def state_sink(state):
            self.saved_states.append(state.to_dict())

        self.assistant = Assistant(
            self.state, self.agents, self.connector,
            FunctionExecutor(provider=self.provider),
            say_sink=say_sink, state_sink=state_sink, **kwargs,
        )

    def user(self, text):
        self.assistant.post({"event": "user_message", "text": text})
        return self


class TestAModelThatDoesNotAnswer:
    """The person is told why, in the provider's own words: a mistyped
    model name must not need the logs to be found."""

    class Refusal(Exception):
        def __init__(self, status_code, said):
            super().__init__(said)
            self.status_code = status_code

    def said_after(self, failure):
        harness = Harness([])

        async def chat(messages, max_tokens=None, tools=None):
            raise failure

        harness.connector.chat = chat
        run(harness.user("hello").assistant.run())
        [said] = harness.said
        return said["text"]

    def test_a_refused_key_says_so_with_the_providers_words(self):
        said = self.said_after(self.Refusal(401, "invalid x-api-key"))
        assert "refused the API key" in said and "invalid x-api-key" in said
        assert "Settings → Model providers" in said

    def test_an_unknown_model_and_an_empty_account_are_told_apart(self):
        assert "does not know this model" in self.said_after(
            self.Refusal(404, "model: nope"))
        assert "out of credit" in self.said_after(self.Refusal(429, "quota"))
        assert "failed on its side" in self.said_after(self.Refusal(503, "busy"))

    def test_a_provider_that_cannot_be_reached(self):
        said = self.said_after(ConnectionError("Connection error."))
        assert "could not be reached at the connection's address" in said

    def test_the_providers_words_are_kept_short(self):
        said = self.said_after(self.Refusal(400, "x" * 5000))
        assert len(said) < 600


class TestTheCycle:
    def test_say_and_finish_answers_and_idles(self):
        harness = Harness([
            action(action="say", text="Hello! How can I help?"),
            action(action="finish"),
        ])
        run(harness.user("hi").assistant.run())
        assert [s["text"] for s in harness.said] == ["Hello! How can I help?"]
        assert harness.saved_states  # persisted every beat

    def test_open_then_invoke_does_real_work(self):
        harness = Harness([
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.note.save",
                   inputs={"notebook": "work", "title": "Ship it"}),
            action(action="say", text="Saved your note."),
            action(action="finish"),
        ])
        run(harness.user("note down: ship it").assistant.run())

        # The record exists — real worker, real provider.
        records = harness.provider.data.get("notebook__note", {})
        assert len(records) == 1
        # The trace recorded the invocation with its agent.
        entry = harness.state.trace[0]
        assert (entry["agent"], entry["status"]) == ("notebook", "success")
        # Evidence rode beside the say: the words are the model's alone,
        # and the write is recorded on the message as a part the page
        # keeps out of the conversation.
        assert harness.said[0]["text"] == "Saved your note."
        assert [p["text"] for p in harness.said[0]["parts"]
                if p["type"] == "success"] == ["Verified: Save Note"]

    def test_a_say_shows_what_the_model_names_and_hears_what_it_cannot(self):
        harness = Harness([
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.note.save",
                   inputs={"notebook": "work", "title": "Ship it"}),
            action(action="invoke", function="notebook.note.find",
                   inputs={"notebook": "work"}),
            action(action="say", text="One note.",
                   show=[{"storage_ref": "stg_found", "title": "Your notes",
                          "columns": ["title"]},
                         {"storage_ref": "stg_nope"}]),
            action(action="finish"),
        ])

        async def store(source, result):
            return "stg_found" if "notes" in result else "stg_saved"

        harness.assistant.executor.storage = store
        run(harness.user("note it, then list").assistant.run())
        tables = [p for p in harness.said[0]["parts"] if p["type"] == "table"]
        # Shown, and whose rows they are: the agent that found them.
        assert tables == [{"type": "table", "text": "Your notes",
                           "storage_ref": "stg_found", "path": "notes",
                           "columns": ["title"],
                           "source": {"kind": "agent", "agent": "notebook",
                                      "agent_name": "Notebook Agent",
                                      "function": "notebook.note.find"}}]
        heard = [m["content"] for m in harness.state.messages
                 if m["role"] == "user" and "not_shown" in m["content"]]
        assert len(heard) == 1 and "stg_nope" in heard[0]

    def test_invoking_an_unopened_agent_is_refused_then_recoverable(self):
        harness = Harness([
            action(action="invoke", function="notebook.note.find", inputs={}),
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.note.find", inputs={}),
            action(action="say", text="No notes yet."),
            action(action="finish"),
        ])
        run(harness.user("any notes?").assistant.run())
        refusal = harness.state.messages[3]["content"]
        assert "not open" in refusal
        assert harness.state.trace[-1]["status"] == "success"

    def test_malformed_output_bounces_without_charge(self):
        """A broken attempt at an action — not prose, which is delivered
        (see below) — is asked again, and the beat is not charged."""
        harness = Harness([
            '{"action": "say", "text": "Here." ',
            action(action="say", text="Here."),
            action(action="finish"),
        ])
        run(harness.user("hi").assistant.run())
        assert harness.said
        bounce = harness.state.messages[3]["content"]
        assert "not a single valid JSON action" in bounce

    def test_free_bounces_are_counted_for_the_ask_not_the_conversation(self):
        """Two corrections are free in one ask. A long conversation
        that used them long ago has them again."""
        broken = '{"action": "say", "text": "Here." '
        harness = Harness([
            broken, broken, action(action="say", text="One.", final=True),
            broken, broken, action(action="say", text="Two.", final=True),
        ])
        run(harness.user("first").assistant.run())
        assert harness.state.beats == 0
        harness.user("second")
        assert harness.assistant._bounces() == 0
        run(harness.assistant.run())
        assert harness.said[-1]["text"] == "Two."

    def test_two_actions_in_one_reply_run_the_first_and_are_told_so(self):
        """A glued "say + finish" is a known habit. Bouncing it invites
        a repeat; taking the first action and saying so does not."""
        harness = Harness([
            action(action="say", text="Reminder: it's time!")
            + " " + action(action="finish"),
            action(action="finish"),
        ])
        run(harness.user("hi").assistant.run())
        assert [s["text"] for s in harness.said] == ["Reminder: it's time!"]
        note = harness.state.messages[3]["content"]
        assert "2 actions; only the first ran" in note
        # Not a bounce: the reply counted as a real beat.
        assert not any("not a single valid JSON action" in m["content"]
                       for m in harness.state.messages)

    def test_a_say_with_nothing_to_say_is_refused_and_the_cycle_goes_on(self):
        """An empty say used to reach the platform, which refused the
        empty message; the refusal escaped as an exception and ended the
        whole cycle mid-turn. Now the model hears it as an observation and
        the next say is delivered as usual."""
        harness = Harness([
            action(action="say", text=""),
            action(action="say", text="Here is what I found."),
            action(action="finish"),
        ])
        run(harness.user("anything?").assistant.run())
        assert [s["text"] for s in harness.said] == ["Here is what I found."]
        transcript = json.dumps(harness.assistant.state.messages)
        assert "A say needs text" in transcript

    def test_a_say_repeated_in_the_same_turn_never_reaches_the_user(self):
        harness = Harness([
            action(action="say", text="Reminder: it's time!"),
            action(action="say", text="Reminder: it's time!"),
            action(action="say", text="Anything else?"),
            action(action="finish"),
        ])
        run(harness.user("hi").assistant.run())
        assert [s["text"] for s in harness.said] == [
            "Reminder: it's time!", "Anything else?"]
        # system, user, say, its observation, the repeated say, refusal.
        refusal = harness.state.messages[5]["content"]
        assert "already told the user that" in refusal

    def test_the_same_thing_in_other_words_is_still_a_repeat(self):
        """The guard compared bytes, so a model told not to repeat itself
        rephrased and was let through — which is the shape the repetition
        actually takes. The words a person reads are what count."""
        harness = Harness([
            action(action="say", text="I'm checking the calendar now."),
            action(action="say", text="I am checking the calendar now!"),
            action(action="say", text="Nothing is booked on Friday."),
            action(action="finish"),
        ])
        run(harness.user("am i free friday?").assistant.run())
        assert [s["text"] for s in harness.said] == [
            "I'm checking the calendar now.", "Nothing is booked on Friday."]

    def test_two_different_messages_both_reach_the_user(self):
        """The guard must not swallow a genuinely new message: refusing
        one is worse than letting a near-duplicate through."""
        harness = Harness([
            action(action="say", text="Nothing is booked on Friday."),
            action(action="say", text="Thursday is full, though."),
            action(action="finish"),
        ])
        run(harness.user("am i free friday?").assistant.run())
        assert [s["text"] for s in harness.said] == [
            "Nothing is booked on Friday.", "Thursday is full, though."]

    def test_a_say_is_observed_so_the_next_beat_starts_on_a_user_turn(self):
        """The transcript must never end on the model's own message
        between beats: a chat model asked to continue from there says it
        again in other words. A say is observed like any other action."""
        harness = Harness([
            action(action="say", text="Two agents are installed."),
            action(action="finish"),
        ])
        run(harness.user("what agents are there?").assistant.run())
        messages = harness.state.messages
        assert messages[2]["role"] == "assistant"
        assert messages[3]["role"] == "user"
        assert messages[3]["content"].startswith("OBSERVATION:")
        assert '"said": true' in messages[3]["content"]
        assert "Finish if the reply is complete" in messages[3]["content"]
        # No two model turns are ever adjacent.
        roles = [m["role"] for m in messages]
        assert all(a != "assistant" or b != "assistant"
                   for a, b in zip(roles, roles[1:]))

    def test_words_with_no_action_reach_the_user_as_a_say(self):
        """A reply that is prose and nothing else is for the user;
        bouncing it lost the answer, because the model then finished."""
        harness = Harness([
            "You have **4 open tickets**, two of them In Progress.",
            action(action="finish"),
        ])
        run(harness.user("what are my open tickets?").assistant.run())
        assert [s["text"] for s in harness.said] == [
            "You have **4 open tickets**, two of them In Progress."]
        messages = harness.state.messages
        # Recorded as the say it became, then observed with a reminder.
        assert json.loads(messages[2]["content"]) == {
            "action": "say",
            "text": "You have **4 open tickets**, two of them In Progress."}
        assert messages[3]["content"].startswith("OBSERVATION:")
        assert "carried no JSON action" in messages[3]["content"]
        assert not any("not a single valid JSON action" in m["content"]
                       for m in messages)

    def test_a_malformed_action_still_bounces(self):
        """An attempt at an action that does not parse is not prose for
        the user: the model is asked again, exactly as before."""
        harness = Harness([
            '{"action": "say", "text": "half a',
            action(action="say", text="Whole."),
            action(action="finish"),
        ])
        run(harness.user("hi").assistant.run())
        assert [s["text"] for s in harness.said] == ["Whole."]
        assert any("not a single valid JSON action" in m["content"]
                   for m in harness.state.messages)

    def test_a_final_say_ends_the_turn_without_another_beat(self):
        """The reply is complete by the model's own word: no beat is
        spent on a finish, and the session goes idle right behind the
        words — which is when the audience's spinner stops."""
        harness = Harness([
            action(action="say", text="Done.", final=True),
            action(action="say", text="Never asked for."),
        ])
        run(harness.user("do it").assistant.run())
        assert [s["text"] for s in harness.said] == ["Done."]
        assert len(harness.connector.calls) == 1
        # The transcript ends on the say; a fresh turn starts clean.
        assert json.loads(harness.state.messages[-1]["content"])["final"] is True

    def test_the_same_words_are_fine_in_a_new_turn(self):
        harness = Harness([
            action(action="say", text="Done."), action(action="finish"),
            action(action="say", text="Done."), action(action="finish"),
        ])
        run(harness.user("do it").assistant.run())
        run(harness.user("again").assistant.run())
        assert [s["text"] for s in harness.said] == ["Done.", "Done."]

    def test_find_files_attaches_what_the_user_chose(self):
        """The model asks for the file the user meant; the session's
        finder puts a card up and answers with what they chose; the
        chosen files enter the transcript as attachments do — a picture
        among them named to be shown — and the observation carries the
        refs the model passes on."""
        asked = []

        async def finder(query, names, kind):
            asked.append((query, names, kind))
            return {"status": "chosen", "files": [
                {"resource_ref": "fil_report", "filename": "report.csv",
                 "file_type": "text/csv", "file_size": 120},
                {"resource_ref": "fil_shot", "filename": "shot.png",
                 "file_type": "image/png", "file_size": 900},
            ]}

        harness = Harness([
            action(action="find_files", query="the sales report",
                   names=["sales", "report"], kind="spreadsheet"),
            action(action="finish"),
        ], file_finder=finder)
        run(harness.user("summarize the sales report").assistant.run())
        assert asked == [
            ("the sales report", ["sales", "report"], "spreadsheet")]
        # system, the user, the action, then the choice and its observation.
        chosen = harness.state.messages[3]
        assert "file_ref fil_report" in chosen["content"]
        assert "[attached: shot.png (image/png) → file_ref fil_shot]" in chosen["content"]
        assert [i["resource_ref"] for i in chosen["images"]] == ["fil_shot"]
        observation = json.loads(harness.state.messages[4]["content"].split("\n", 1)[1])
        assert [f["file_ref"] for f in observation["files"]] == ["fil_report", "fil_shot"]

    def test_find_files_with_nothing_chosen_says_so(self):
        async def finder(query, names, kind):
            return {"status": "declined", "files": []}

        harness = Harness([
            action(action="find_files", query="the report"),
            action(action="finish"),
        ], file_finder=finder)
        run(harness.user("summarize the report").assistant.run())
        observation = json.loads(harness.state.messages[3]["content"].split("\n", 1)[1])
        assert observation["files"] == [] and "no files" in observation["note"]

    def test_find_files_needs_a_query_and_a_finder(self):
        harness = Harness([
            action(action="find_files"),
            action(action="finish"),
        ])
        run(harness.user("hi").assistant.run())
        observation = json.loads(harness.state.messages[3]["content"].split("\n", 1)[1])
        assert "needs a query" in observation["error"]

        harness = Harness([
            action(action="find_files", query="x"),
            action(action="finish"),
        ])
        run(harness.user("hi").assistant.run())
        observation = json.loads(harness.state.messages[3]["content"].split("\n", 1)[1])
        assert "cannot be looked up" in observation["error"]

    @staticmethod
    def _reader(files):
        import base64

        async def read(ref):
            record = files.get(ref)
            if record is None:
                return {}
            raw = record["content"]
            return {"filename": record["filename"],
                    "file_type": record.get("file_type", ""),
                    "file_size": len(raw),
                    "content_base64": base64.b64encode(raw).decode("ascii")}
        return read

    def test_read_file_puts_a_documents_text_in_front_of_the_model(self):
        reader = self._reader({"fil_report": {
            "filename": "report.csv", "file_type": "text/csv",
            "content": b"region,total\nnorth,12\nsouth,30\n"}})
        harness = Harness([
            action(action="read_file", file_ref="fil_report"),
            action(action="finish"),
        ], file_reader=reader)
        run(harness.user("summarize the report").assistant.run())
        observation = json.loads(harness.state.messages[3]["content"].split("\n", 1)[1])
        assert observation["text"] == "region,total\nnorth,12\nsouth,30\n"
        assert (observation["filename"], observation["complete"]) == ("report.csv", True)

    def test_a_long_document_is_read_a_page_at_a_time(self):
        from ai_runtime.reasoning.documents import DocumentPage

        # Two pages exactly: 18,000 characters against a 12,000 window.
        words = ("lorem ipsum " * 1500).encode("utf-8")
        reader = self._reader({"fil_long": {
            "filename": "long.txt", "file_type": "text/plain", "content": words}})
        harness = Harness([
            action(action="read_file", file_ref="fil_long"),
            action(action="read_file", file_ref="fil_long", **{"from": DocumentPage.MAX_CHARS}),
            action(action="finish"),
        ], file_reader=reader)
        run(harness.user("read it").assistant.run())
        first = json.loads(harness.state.messages[3]["content"].split("\n", 1)[1])
        second = json.loads(harness.state.messages[5]["content"].split("\n", 1)[1])
        assert len(first["text"]) == DocumentPage.MAX_CHARS
        assert first["next_from"] == DocumentPage.MAX_CHARS and "complete" not in first
        assert second["from"] == DocumentPage.MAX_CHARS
        assert first["text"] + second["text"] == words.decode("utf-8")
        assert second["complete"] is True
        assert len(harness.state.messages[3]["content"]) < Assistant.OBSERVATION_MAX_CHARS

    def test_read_file_declines_what_is_not_a_text_document(self):
        reader = self._reader({
            "fil_pic": {"filename": "shot.png", "file_type": "image/png",
                        "content": b"\x89PNG pixels"},
            "fil_sheet": {"filename": "book.xlsx", "content": b"PK\x03\x04junk",
                          "file_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
        })
        harness = Harness([
            action(action="read_file", file_ref="fil_pic"),
            action(action="read_file", file_ref="fil_sheet"),
            action(action="read_file", file_ref="fil_nowhere"),
            action(action="read_file"),
            action(action="finish"),
        ], file_reader=reader)
        run(harness.user("read them").assistant.run())
        errors = [json.loads(harness.state.messages[i]["content"].split("\n", 1)[1])["error"]
                  for i in (3, 5, 7, 9)]
        assert "shown to you as a picture" in errors[0]
        assert "spreadsheet" in errors[1] and "agent" in errors[1]
        assert "No file 'fil_nowhere'" in errors[2]
        assert "needs a file_ref" in errors[3]

    def test_unknown_action_is_corrected(self):
        harness = Harness([
            action(action="delegate", agent="notebook", goal="save a note"),
            action(action="say", text="Understood."),
            action(action="finish"),
        ])
        run(harness.user("hi").assistant.run())
        correction = harness.state.messages[3]["content"]
        assert "Unknown action 'delegate'" in correction

    def test_plan_is_kept_and_settled(self):
        shown = []

        async def plan_sink(steps):
            shown.append(steps)

        harness = Harness([
            action(action="plan", steps=["Save the note", "Confirm"]),
            action(action="plan", step=1, status="done"),
            action(action="finish", reason="awaiting_user"),
        ], plan_sink=plan_sink)
        run(harness.user("do two things").assistant.run())
        assert len(shown) == 2
        first = harness.state.plan.to_steps()[0]
        assert first["status"] == "done" and first["id"] == "w1"
        assert first["verified"] is False           # done by word alone
        assert not harness.state.plan.finished()
        # The frame's PLAN is the plan as it now stands, not the one
        # the mind was framed with: rewritten on every plan action.
        frame = harness.state.messages[0]["content"]
        assert "(no plan yet)" not in frame
        assert "Save the note" in frame and "Confirm" in frame


class TestPlanLifecycle:
    """A plan belongs to the ask it answered: one whose every item is
    done is cleared when the person asks again; one still open — a
    blocked item waiting on the person most of all — spans the
    messages it takes."""

    @staticmethod
    def harness(script):
        shown = []

        async def plan_sink(steps):
            shown.append(steps)

        return Harness(script, plan_sink=plan_sink), shown

    def test_a_done_plan_is_cleared_by_the_next_ask(self):
        harness, shown = self.harness([
            action(action="plan", steps=["Find it", "Say it"]),
            action(action="plan", item="w1", status="done"),
            action(action="plan", item="w2", status="done"),
            action(action="say", text="Done.", final=True),
            action(action="say", text="Hello again.", final=True),
        ])
        run(harness.user("do it").assistant.run())
        assert [s["id"] for s in shown[-1]] == ["w1", "w2"]
        run(harness.user("something else").assistant.run())
        assert shown[-1] == []                          # the page was told
        assert harness.state.plan.to_steps() == []
        assert "(no plan yet)" in harness.state.messages[0]["content"]

    def test_a_blocked_plan_survives_the_answer(self):
        harness, shown = self.harness([
            action(action="plan", steps=["Find it", "Confirm with the user"]),
            action(action="plan", item="w1", status="done"),
            action(action="plan", item="w2", status="blocked",
                   blocker="which notebook?"),
            action(action="finish", reason="awaiting_user"),
            action(action="plan", item="w2", status="done"),
            action(action="say", text="Confirmed.", final=True),
        ])
        run(harness.user("do it").assistant.run())
        run(harness.user("the work notebook").assistant.run())
        assert [(s["id"], s["status"]) for s in harness.state.plan.to_steps()] == [
            ("w1", "done"), ("w2", "done")]
        assert [] not in shown                          # never cleared

    def test_an_open_plan_survives_an_interjection(self):
        harness, shown = self.harness([
            action(action="plan", steps=["Find it", "Say it"]),
            action(action="plan", item="w1", status="done"),
            action(action="finish", reason="awaiting_user"),
            action(action="say", text="Still on it.", final=True),
        ])
        run(harness.user("do it").assistant.run())
        run(harness.user("any news?").assistant.run())
        assert [s["status"] for s in harness.state.plan.to_steps()] == ["done", "pending"]


class TestWorkItems:
    """The plan as a record the code enforces: evidence lands on the
    item in progress, a finish claiming completion is refused while
    items are owed, and invented evidence is refused."""

    @staticmethod
    def observations(harness):
        return [m["content"] for m in harness.state.messages
                if m["role"] == "user" and m["content"].startswith("OBSERVATION")]

    def test_evidence_lands_on_the_active_item(self):
        harness = Harness([
            action(action="plan", steps=["Save the note", "Tell the user"]),
            action(action="plan", item="w1", status="active"),
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.note.save",
                   inputs={"notebook": "work", "title": "Milk"}),
            action(action="plan", item="w1", status="done"),
            action(action="finish", reason="awaiting_user"),
        ])

        async def store(source, result):        # results get a ref
            return "stg_saved"

        harness.assistant.executor.storage = store
        run(harness.user("save a note").assistant.run())
        saved, telling = harness.state.plan.to_steps()
        assert harness.state.trace[-1]["result"]["storage_ref"] == "stg_saved"
        assert saved["status"] == "done" and saved["verified"] is True
        assert saved["evidence"] == ["stg_saved"]
        assert telling["evidence"] == []               # not active then
        assert f"w1 [done, 1 evidence]" in harness.state.messages[0]["content"]

    def test_completed_is_refused_while_items_are_owed(self):
        harness = Harness([
            action(action="plan", steps=["One", "Two"]),
            action(action="plan", item="w1", status="done"),
            action(action="finish"),                    # claims completed
            action(action="finish", reason="awaiting_user"),
        ])
        run(harness.user("two things").assistant.run())
        refused = [o for o in self.observations(harness) if "w2 still owed" in o]
        assert len(refused) == 1
        assert harness.state.beats == 0                # the honest one landed

    def test_a_final_say_is_delivered_but_does_not_end_owed_work(self):
        harness = Harness([
            action(action="plan", steps=["One"]),
            action(action="say", text="All done!", final=True),
            action(action="plan", item="w1", status="done"),
            action(action="finish", reason="completed"),
        ])
        run(harness.user("do one thing").assistant.run())
        assert [s["text"] for s in harness.said] == ["All done!"]
        assert any("w1 still owed" in o for o in self.observations(harness))
        assert harness.state.plan.finished()

    def test_invented_evidence_and_a_bare_block_are_refused(self):
        harness = Harness([
            action(action="plan", steps=["One"]),
            action(action="plan", item="w1", status="done",
                   evidence=["stg_made_up"]),
            action(action="plan", item="w1", status="blocked"),
            action(action="plan", item="w1", status="blocked",
                   blocker="no such notebook"),
            action(action="finish", reason="blocked"),
        ])
        run(harness.user("one thing").assistant.run())
        seen = self.observations(harness)
        assert any("stg_made_up is not in this chat's trace" in o for o in seen)
        assert any("needs a blocker" in o for o in seen)
        item = harness.state.plan.to_steps()[0]
        assert item["status"] == "blocked"
        assert item["blocker"] == "no such notebook"

    def test_awaiting_events_needs_something_to_wait_for(self):
        harness = Harness([
            action(action="finish", reason="awaiting_events"),
            action(action="finish", reason="completed"),
        ])
        run(harness.user("hi").assistant.run())
        assert any("Nothing to await" in o for o in self.observations(harness))


class TestJobs:
    def test_a_job_runs_while_the_assistant_finishes_and_wakes_it(self):
        harness = Harness([
            action(action="open_agent", agent="notebook"),
            action(action="start", function="notebook.note.find", inputs={}),
            action(action="finish"),                    # idle-until-event
            action(action="say", text="Search done — nothing yet."),
            action(action="finish"),
        ])
        run(harness.user("search in the background").assistant.run())

        job = list(harness.state.jobs.values())[0]
        assert job.status == "done"
        # The completion arrived as an event the model was woken with.
        event = next(
            m for m in harness.state.messages
            if "EVENT job_done" in str(m.get("content"))
        )
        assert job.job_id in event["content"]
        assert harness.said[-1]["text"].startswith("Search done")

    def test_two_jobs_run_concurrently_on_one_worker(self):
        harness = Harness([
            action(action="open_agent", agent="notebook"),
            action(action="start", function="notebook.note.find", inputs={}),
            action(action="start", function="notebook.note.find",
                   inputs={"notebook": "work"}),
            action(action="finish"),
            action(action="finish"),   # woken by first completion; wait more
            action(action="say", text="Both searches finished."),
            action(action="finish"),
        ])
        run(harness.user("two searches").assistant.run())
        statuses = [job.status for job in harness.state.jobs.values()]
        assert statuses == ["done", "done"]
        assert len(harness.state.trace) == 2

    def test_stop_cancels_jobs_and_idles(self):
        harness = Harness([
            action(action="open_agent", agent="notebook"),
            action(action="start", function="notebook.note.find", inputs={}),
        ])
        # Post the stop before running: absorbed after the second beat's
        # persist, before any third model call — the script has no third
        # entry, which is the proof it was never consulted again.
        harness.user("search")
        harness.assistant.post({"event": "stop"})
        run(harness.assistant.run())


class TestInterjection:
    def test_a_user_message_mid_work_is_absorbed_not_refused(self):
        """The chat_busy wall is gone: the person speaks while a job
        runs, and the assistant is woken holding both the interjection
        and, later, the job's completion."""
        harness = Harness([
            action(action="open_agent", agent="notebook"),
            action(action="start", function="notebook.note.find", inputs={}),
            action(action="finish"),
            action(action="say", text="Yes — still searching."),
            action(action="finish"),
            action(action="say", text="Done."),
            action(action="finish"),
        ])
        harness.user("search my notes")

        async def scenario():
            task = asyncio.create_task(harness.assistant.run())
            await asyncio.sleep(0)
            harness.assistant.post(
                {"event": "user_message", "text": "you still there?"})
            await task

        run(scenario())
        transcript = [str(m.get("content")) for m in harness.state.messages]
        assert any("you still there?" in content for content in transcript)
        assert "Yes — still searching." in [s["text"] for s in harness.said]

    def test_the_frame_tells_the_model_to_account_for_every_call(self):
        """Two requests in one turn, a call that ran and a repeat that
        was refused: the answer must name both. The model reported only
        the refusal once, "no order was placed", after the order was
        placed — so the rule is in the frame it reads."""
        harness = Harness([action(action="finish")])
        run(harness.user("order it").assistant.run())
        [frame] = [m["content"] for m in harness.state.messages
                   if m["role"] == "system"]
        assert "The answer accounts for each" in frame
        assert "A refused repeat does not undo what an earlier call did" in frame
        # A site's own agent before a browser.
        assert "When a site has its own agent installed" in frame


class TestTheValve:
    def test_exhaustion_reports_and_remains_continuable(self):
        looping = [action(action="open_agent", agent="notebook")] * 10
        harness = Harness(looping, max_beats=3)
        run(harness.user("loop forever").assistant.run())
        assert "paused here" in harness.said[-1]["text"]
        # The state survives — beats reset, transcript intact, nothing
        # discarded.
        assert harness.state.messages
        wrap_up = next(
            (m for m in harness.state.messages
             if "Wrap up now" in str(m.get("content"))), None)
        assert wrap_up is not None

    def test_a_helper_out_of_beats_reports_that_and_not_done(self):
        """`budget` is the valve's word: a helper that ran out of beats
        says so in its report, and its parent reads why."""
        finished = []

        async def finish_sink(summary, reason):
            finished.append(reason)
        looping = [action(action="open_agent", agent="notebook")] * 10
        harness = Harness(looping, max_beats=3, finish_sink=finish_sink)
        run(harness.user("loop forever").assistant.run())
        assert finished == ["budget"]

    def test_budget_is_not_a_reason_the_model_may_give(self):
        harness = Harness([
            action(action="finish", reason="budget"),
            action(action="finish"),
        ])
        run(harness.user("hello").assistant.run())
        assert any("finish needs a reason" in str(m.get("content"))
                   for m in harness.state.messages)

    def test_zero_is_no_valve_at_all(self):
        """The person chose unlimited: sixty beats past the standard
        budget pause nothing and say nothing about pausing."""
        long_work = [action(action="open_agent", agent="notebook")] * 60
        harness = Harness(long_work + [action(action="finish")], max_beats=0)
        run(harness.user("keep going").assistant.run())
        assert harness.state.beats == 0  # finished, reset
        assert not any("paused here" in s["text"] for s in harness.said)
        assert not any("Wrap up now" in str(m.get("content"))
                       for m in harness.state.messages)

    def test_a_user_message_resets_the_valve(self):
        harness = Harness([
            action(action="say", text="one"),
            action(action="finish"),
            action(action="say", text="two"),
            action(action="finish"),
        ], max_beats=3)
        run(harness.user("first").assistant.run())
        assert harness.state.beats == 0
        run(harness.user("second").assistant.run())
        assert [s["text"] for s in harness.said] == ["one", "two"]


class TestDurability:
    def test_the_mind_round_trips_whole(self):
        harness = Harness([
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.note.save",
                   inputs={"notebook": "work", "title": "Persist me"}),
            action(action="plan", steps=["a", "b"]),
            action(action="finish", reason="awaiting_user"),
        ])
        run(harness.user("save and plan").assistant.run())

        revived = AssistantState.from_dict(harness.state.to_dict())
        assert revived.opened == ["notebook"]
        assert revived.trace[0]["function"] == "notebook.note.save"
        assert [s["text"] for s in revived.plan.to_steps()] == ["a", "b"]
        assert revived.messages == harness.state.messages

    def test_a_rehydrated_mind_continues_the_conversation(self):
        first = Harness([
            action(action="say", text="Noted — ask me anytime."),
            action(action="finish"),
        ])
        run(first.user("remember the context").assistant.run())

        revived = AssistantState.from_dict(first.state.to_dict())
        second = Harness([
            action(action="say", text="As I said, ask away."),
            action(action="finish"),
        ], state=revived)
        run(second.user("and now?").assistant.run())
        # No fresh system frame was built: the transcript continued.
        assert revived.messages[0]["role"] == "system"
        assert sum(1 for m in revived.messages
                   if m["role"] == "system") == 1


class TestCompaction:
    """The trace stays bounded without ever losing unpresented work."""

    def entries(self, count, job_prefix=""):
        return [{"agent": "notebook", "function": "notebook.note.find",
                 "inputs": {}, "status": "success", "result": {"i": i},
                 **({"job_id": f"{job_prefix}{i}"} if job_prefix else {})}
                for i in range(count)]

    def test_presented_entries_beyond_the_keep_are_dropped(self):
        from ai_runtime.reasoning.state import DONE, RUNNING, Job

        state = AssistantState(trace=self.entries(150, "job_"),
                               evidence_cursor=140)
        state.TRACE_KEEP = 100
        state.jobs = {
            "job_3": Job("job_3", "notebook", "notebook.note.find", {},
                         status=DONE),                # entry dropped
            "job_120": Job("job_120", "notebook", "notebook.note.find",
                           {}, status=DONE),          # entry kept
            "job_live": Job("job_live", "notebook", "notebook.note.find",
                            {}, status=RUNNING),      # active: kept
        }

        assert state.compact() is True
        assert len(state.trace) == 100
        assert state.trace[0]["result"] == {"i": 50}
        assert state.evidence_cursor == 90
        assert set(state.jobs) == {"job_120", "job_live"}
        # Idempotent once within bounds.
        assert state.compact() is False

    def test_unpresented_work_is_never_dropped(self):
        state = AssistantState(trace=self.entries(150), evidence_cursor=10)
        state.TRACE_KEEP = 100

        assert state.compact() is True
        # Only the ten presented entries could go.
        assert len(state.trace) == 140
        assert state.evidence_cursor == 0
        assert state.compact() is False


class TestParsingReplies:
    """What a reply may look like and still be understood."""

    def test_prose_around_one_object(self):
        assert Assistant.parse_actions(
            'Sure. {"action": "say", "text": "hi"} Done.'
        ) == [{"action": "say", "text": "hi"}]

    def test_fenced_json(self):
        assert Assistant.parse_actions(
            '```json\n{"action": "finish"}\n```') == [{"action": "finish"}]

    def test_several_objects_in_order_with_braces_inside_strings(self):
        reply = ('{"action": "say", "text": "a {b} c"}\n'
                 '{"action": "finish"}')
        assert Assistant.parse_actions(reply) == [
            {"action": "say", "text": "a {b} c"}, {"action": "finish"}]

    def test_junk_braces_before_the_object_are_skipped(self):
        assert Assistant.parse_actions(
            '{oops} {"action": "finish"}') == [{"action": "finish"}]

    def test_nothing_parseable_is_nothing(self):
        assert Assistant.parse_actions("let me think") == []
        assert Assistant.parse_actions("[1, 2]") == []

    def test_an_object_that_names_no_action_is_prose(self):
        """A JSON example in an answer is part of the answer."""
        assert Assistant.parse_actions(
            'Send it as {"to": "a@x.example"} and it goes.') == []


class TestEvidence:
    """What may appear as data beside the model's words — one voice,
    one table at most, and only what the trace proves."""

    @staticmethod
    def agents():
        agents, errors = load_agents(AGENTS_DIR)
        assert errors == {}
        return agents

    @staticmethod
    def read(ref, rows):
        return {"agent": "notebook", "function": "notebook.note.find",
                "inputs": {}, "status": "success",
                "result": {"notes": rows, "total": len(rows),
                           "storage_ref": ref}}

    @staticmethod
    def write(ref):
        return {"agent": "notebook", "function": "notebook.note.save",
                "inputs": {}, "status": "success",
                "result": {"note_ref": "n1", "created": True,
                           "storage_ref": ref}}

    @staticmethod
    def offered(ref, displays, status="success"):
        return {"agent": "notebook", "function": "notebook.note.find",
                "inputs": {}, "status": status,
                "result": {"notes": [], "total": 0, "storage_ref": ref,
                           "displays": displays}}

    def test_a_large_result_is_proved_whole_though_it_is_kept_cut(self):
        """The trace keeps a cut copy of a large result. What a message
        says about it — how many rows a read found, which displays the
        call offered — is taken from the whole one."""
        from ai_runtime.reasoning.evidence import Evidence

        harness = Harness([])
        rows = [{"note_ref": f"n{i}", "title": f"note {i}", "notebook": "work",
                 "priority": None, "body": "x" * 200} for i in range(100)]
        displays = [{"display_id": f"stg_d{i}", "kind": "table",
                     "title": "t" * 300} for i in range(6)]
        harness.assistant._record(
            "notebook", "notebook.note.find", {},
            {"notes": rows, "total": 100, "storage_ref": "stg_r",
             "displays": displays}, "success")

        [entry] = harness.state.trace
        kept = entry["result"]
        assert kept["truncated"] is True and len(kept["notes"]) < 100
        # Every display the call offered can still be shown...
        assert [d["display_id"] for d in kept["displays"]] == [
            f"stg_d{i}" for i in range(6)]
        composed = Evidence.compose(
            "Here.", harness.agents, harness.state.trace,
            show=[{"display": "stg_d5"}])
        assert composed["refused"] == []
        # ...and a read is counted by what it found, not by what fitted.
        [read] = Evidence.reads(harness.agents, harness.state.trace)
        assert read["count"] == 100
        silent = Evidence.compose("", harness.agents, harness.state.trace)
        assert silent["text"] == "Found 100 results."

    def test_a_display_a_call_offered_is_shown_as_the_model_names_it(self):
        from ai_runtime.reasoning.evidence import Evidence

        found = self.offered("stg_r", [
            {"display_id": "stg_t", "kind": "table", "title": "Notes"},
            {"display_id": "stg_c", "kind": "chart", "title": "By notebook"}])
        composed = Evidence.compose(
            "Here they are.", self.agents(), [found],
            show=[{"display": "stg_t"},
                  {"display": "stg_c", "title": "Notes per notebook"},
                  {"display": "stg_nope"}])
        source = {"kind": "agent", "agent": "notebook",
                  "agent_name": "Notebook Agent",
                  "function": "notebook.note.find"}
        assert composed["parts"] == [
            {"type": "table", "text": "Notes", "storage_ref": "stg_t",
             "source": source},
            {"type": "graph", "text": "Notes per notebook",
             "storage_ref": "stg_c", "source": source}]
        assert len(composed["refused"]) == 1
        assert "stg_nope" in composed["refused"][0]

    def test_a_failed_calls_display_is_never_shown(self):
        from ai_runtime.reasoning.evidence import Evidence

        failed = self.offered("stg_r", [
            {"display_id": "stg_t", "kind": "table", "title": "Notes"}],
            status="error")
        composed = Evidence.compose("Hm.", self.agents(), [failed],
                                    show=[{"display": "stg_t"}])
        assert composed["parts"] == []
        assert composed["refused"]

    def test_a_shown_result_is_attached_as_the_model_presents_it(self):
        from ai_runtime.reasoning.evidence import Evidence

        probe = self.read("stg_probe", [{"title": "a"}])
        answer = self.read("stg_answer", [{"title": "b"}, {"title": "c"}])
        composed = Evidence.compose(
            "Two notes.", self.agents(), [probe, answer],
            show=[{"storage_ref": "stg_answer", "title": "Notes found",
                   "columns": ["title", "", "title"]}])
        assert composed["text"] == "Two notes."
        assert composed["parts"] == [{
            "type": "table", "text": "Notes found",
            "storage_ref": "stg_answer", "path": "notes",
            "columns": ["title"],                   # the path found for it
            "source": {"kind": "agent", "agent": "notebook",
                       "agent_name": "Notebook Agent",
                       "function": "notebook.note.find"}}]
        assert composed["refused"] == []

    def test_nothing_is_attached_unless_shown(self):
        from ai_runtime.reasoning.evidence import Evidence

        answer = self.read("stg_answer", [{"title": "b"}, {"title": "c"}])
        composed = Evidence.compose("Two notes.", self.agents(), [answer])
        assert composed == {"text": "Two notes.", "parts": [], "refused": []}

    def test_a_show_the_trace_cannot_vouch_for_is_refused_and_said_back(self):
        from ai_runtime.reasoning.evidence import Evidence

        empty = self.read("stg_empty", [])
        answer = self.read("stg_answer", [{"title": "b"}])
        composed = Evidence.compose(
            "Here.", self.agents(), [empty, answer],
            show=[{"storage_ref": "stg_invented"},
                  {"storage_ref": "stg_empty"},
                  {"storage_ref": "stg_answer", "path": "total"}])
        assert composed["parts"] == []
        assert [r.split(" ")[0] for r in composed["refused"]] == [
            "'stg_invented'", "'stg_empty'", "'total'"]
        assert "not a stored result" in composed["refused"][0]
        assert "no rows" in composed["refused"][1]
        assert "not a list field" in composed["refused"][2]

    def test_a_shown_result_may_come_from_before_the_last_say(self):
        from ai_runtime.reasoning.evidence import Evidence

        earlier = self.read("stg_earlier", [{"title": "a"}])
        composed = Evidence.compose(
            "As I found earlier.", self.agents(), [],
            show=[{"storage_ref": "stg_earlier"}], history=[earlier])
        assert composed["parts"] == [{
            "type": "table", "text": "Find Notes",
            "storage_ref": "stg_earlier", "path": "notes",
            "source": {"kind": "agent", "agent": "notebook",
                       "agent_name": "Notebook Agent",
                       "function": "notebook.note.find"}}]

    def test_a_write_is_recorded_not_spoken(self):
        from ai_runtime.reasoning.evidence import Evidence

        composed = Evidence.compose(
            "Saved it.", self.agents(), [self.write("stg_w")])
        assert composed["text"] == "Saved it."
        [part] = composed["parts"]
        assert {key: value for key, value in part.items()
                if key != "source"} == {
            "type": "success", "text": "Verified: Save Note",
            "storage_ref": "stg_w"}
        # Whose write it was rides the part.
        assert part["source"]["kind"] == "agent"

    def test_the_runtime_speaks_only_for_a_silent_model(self):
        from ai_runtime.reasoning.evidence import Evidence

        agents = self.agents()
        assert Evidence.compose("", agents, [self.write("stg_w")])["text"] == \
            "Verified: Save Note."
        assert Evidence.compose("", agents, [self.read("stg_1", [{}, {}])])["text"] == \
            "Found 2 results."
        assert Evidence.compose("", agents, [self.read("stg_1", [])])["text"] == \
            "The search ran and came back empty — nothing matches yet."


class TestToolsOffered:
    def test_every_beat_offers_the_actions_as_tools(self):
        from ai_runtime.reasoning.actions import ACTION_NAMES, ACTION_TOOLS

        harness = Harness([action(action="finish")])
        run(harness.user("hi").assistant.run())
        assert harness.connector.calls[0]["tools"] == ACTION_TOOLS
        assert set(ACTION_NAMES) == {
            "say", "open_agent", "invoke", "start", "cancel_job", "read",
            "find_files", "read_file", "use_skill", "recall", "remember",
            "plan", "schedule", "unschedule", "sleep", "spawn", "finish",
            "close_agent", "find_agents"}
        # Every tool is a schema the provider can validate against.
        for tool in ACTION_TOOLS:
            parameters = tool["function"]["parameters"]
            assert parameters["type"] == "object"
            assert set(parameters["required"]) <= set(parameters["properties"])

    def test_an_opened_agents_functions_are_tools_of_their_own(self):
        from ai_runtime.reasoning.actions import ACTION_TOOLS

        harness = Harness([
            action(action="open_agent", agent="notebook"),
            action(action="finish"),
        ])
        run(harness.user("hi").assistant.run())
        before, after = (call["tools"] for call in harness.connector.calls)
        assert before == ACTION_TOOLS                 # nothing open yet
        offered = {t["function"]["name"]: t["function"]
                   for t in after[len(ACTION_TOOLS):]}
        save = offered["notebook__note__save"]
        _, declared = harness.agents["notebook"].manifest.function(
            "notebook.note.save")
        # The manifest's schema, whole — not a shape summary.
        assert save["parameters"] == declared["inputs"]
        assert "Create a note" in save["description"]
        assert "Returns {" in save["description"]
        # And the catalog text no longer carries what the tools do.
        catalog = harness.assistant._render_catalog(harness.agents["notebook"])
        assert "notebook.note.save" in catalog
        assert "inputs:" not in catalog

    def test_the_cap_is_as_many_tools_as_a_provider_accepts(self):
        """OpenAI-compatible providers refuse more than 128 tools in one
        request; the actions and the functions together must fit."""
        from ai_runtime.reasoning.actions import ACTION_TOOLS, FunctionTools
        assert len(ACTION_TOOLS) + FunctionTools.MAX_FUNCTIONS == 128

    def test_past_the_cap_the_newest_agent_is_offered_and_the_rest_named(self):
        """The cap used to drop the agent opened LAST — the one the model
        had just read the instructions of and was about to call — and the
        model concluded a mail agent had no send function. Now the newest
        agent's functions are the tools, and the ones that did not fit
        are named once, as callable by name."""
        from ai_runtime.reasoning.actions import ACTION_TOOLS, FunctionTools

        kept = FunctionTools.MAX_FUNCTIONS
        FunctionTools.MAX_FUNCTIONS = 3       # llm has 3 functions, notebook 7
        try:
            harness = Harness([
                action(action="open_agent", agent="notebook"),
                action(action="open_agent", agent="llm"),
                action(action="say", text="Both open."),
                action(action="finish"),
            ])
            run(harness.user("open both").assistant.run())
        finally:
            FunctionTools.MAX_FUNCTIONS = kept

        offered = [t["function"]["name"] for t in
                   harness.connector.calls[-1]["tools"][len(ACTION_TOOLS):]]
        assert offered and all(name.startswith("llm__") for name in offered)
        notes = [m["content"] for m in harness.state.messages
                 if m["role"] == "user" and "invoke action by name" in m["content"]]
        # Said when the list changes — notebook alone, then notebook
        # behind llm — never repeated on a beat where nothing changed.
        assert len(notes) == 2
        assert "notebook.note.save" in notes[-1] and "llm." not in notes[-1]

    def test_a_call_by_tool_name_is_an_invoke(self):
        harness = Harness([
            action(action="open_agent", agent="notebook"),
            action(action="notebook__note__save", notebook="work",
                   title="Milk"),
            action(action="finish"),
        ])
        run(harness.user("save a note").assistant.run())
        entry = harness.state.trace[-1]
        assert entry["function"] == "notebook.note.save"
        assert entry["status"] == "success"
        # The transcript keeps the one vocabulary: the call was recorded
        # as the invoke it is, never under the tool's encoded name.
        recorded = [json.loads(m["content"]) for m in harness.state.messages
                    if m["role"] == "assistant"
                    and m["content"].startswith("{")]
        assert {"action": "invoke", "function": "notebook.note.save",
                "inputs": {"notebook": "work", "title": "Milk"}} in recorded
        assert not any("notebook__note__save" in m["content"]
                       for m in harness.state.messages)

    def test_a_tool_name_fits_the_provider_and_finds_its_way_back(self):
        import re
        from ai_runtime.reasoning.actions import TOOL_NAME_MAX, FunctionTools

        assert FunctionTools.encode("agt_x.note.save") == "agt_x__note__save"
        long = ("agt_0ba09c35d8324040b6bd.a_very_long_tool_identifier"
                ".a_very_long_function_identifier_indeed")
        name = FunctionTools.encode(long)
        assert len(name) <= TOOL_NAME_MAX
        assert re.fullmatch(r"[A-Za-z0-9_-]+", name)
        assert FunctionTools.encode(long) == name          # stable
        tools = FunctionTools({}, [], 1)
        tools.names[name] = long
        assert tools.as_action({"action": name, "a": 1}) == {
            "action": "invoke", "function": long, "inputs": {"a": 1}}
        assert tools.as_action({"action": "say", "text": "hi"}) == {
            "action": "say", "text": "hi"}

    def test_a_screen_shown_on_request_is_not_the_models_to_call(self):
        """The model invoked a browser's watch so the person could sign
        in: the call held the turn for nine minutes and what they wrote
        meanwhile went unheard. A function the platform calls when the
        person opens a screen is neither offered as a tool nor listed,
        and called by name it is refused with what to do instead."""
        from ai_runtime.reasoning.actions import ACTION_TOOLS

        harness = Harness([
            action(action="open_agent", agent="screen"),
            action(action="invoke", function="screen.show.watch",
                   inputs={"action": "open"}),
            action(action="start", function="screen.show.watch", inputs={}),
            action(action="say", text="Please open the live view and sign in."),
            action(action="finish", reason="awaiting_user"),
        ])
        run(harness.user("sign me in").assistant.run())

        offered = [t["function"]["name"] for t in
                   harness.connector.calls[-1]["tools"][len(ACTION_TOOLS):]]
        assert offered == ["screen__show__run"]
        opened = next(m["content"] for m in harness.state.messages
                      if m["role"] == "user" and "catalog" in m["content"])
        assert "screen.show.run" in opened and "screen.show.watch" not in opened
        refused = [m["content"] for m in harness.state.messages
                   if m["role"] == "user" and "theirs to open" in m["content"]]
        assert len(refused) == 2
        assert harness.state.trace == [] and not harness.state.jobs
        assert [s["text"] for s in harness.said] == [
            "Please open the live view and sign in."]

    def test_an_input_named_action_does_not_replace_the_action(self):
        """A browser's watch takes one input, named action (open or
        quit). Spread beside the call's name it replaced it: the model
        called the right tool and was told eighteen times that 'open'
        is an action nobody knows. The name stays the action, and the
        input arrives under its own name."""
        from ai_runtime.llms.connector.tools import ModelReply
        from ai_runtime.reasoning.actions import FunctionTools

        tools = FunctionTools({}, [], 1)
        tools.names["agt_x__browse__watch"] = "agt_x.browse.watch"
        reply = ModelReply(calls=[{"name": "agt_x__browse__watch",
                                   "arguments": '{"action": "open", "tab": 2}'}])
        [read] = [json.loads(line) for line in reply.content.splitlines()]
        assert read["action"] == "agt_x__browse__watch"
        assert tools.as_action(read) == {
            "action": "invoke", "function": "agt_x.browse.watch",
            "inputs": {"action": "open", "tab": 2}}
        # An action of the cycle's own is read as it always was.
        said = ModelReply(calls=[{"name": "say", "arguments": {"text": "hi", "final": True}}])
        assert json.loads(said.content) == {"action": "say", "text": "hi", "final": True}


class TestObservationBudget:
    """What the model sees of a result: whole when it fits, and the
    first complete items when it does not — never one row at a time."""

    @staticmethod
    def rows(count, size=160):
        return [{"key": f"MI-{i}", "summary": "x" * size, "status": "Open"}
                for i in range(count)]

    def test_the_trace_keeps_a_large_result_bounded(self):
        # One read of 600 long rows is over the platform's whole-state
        # cap by itself. The trace keeps the shape and the reference —
        # rows still a list, storage_ref still there — never the payload.
        from ai_runtime.reasoning.evidence import Evidence

        mind = Harness([action(action="finish")]).assistant
        big = {"notes": self.rows(600, 1000), "total": 600,
               "storage_ref": "stg_big"}
        mind._record("notebook", "notebook.note.find", {}, big, "success")
        kept = mind.state.trace[-1]["result"]
        assert kept["truncated"] is True
        assert kept["storage_ref"] == "stg_big"
        assert isinstance(kept["notes"], list) and 0 < len(kept["notes"]) < 600
        assert mind.state.serialized_size() < 64 * 1024
        # And evidence still attaches the table from storage.
        parts = Evidence.compose("Here.", mind.agents, mind.state.trace,
                                 show=[{"storage_ref": "stg_big"}])["parts"]
        assert parts == [{"type": "table", "text": "Find Notes",
                          "storage_ref": "stg_big", "path": "notes",
                          "source": {"kind": "agent", "agent": "notebook",
                       "agent_name": "Notebook Agent",
                       "function": "notebook.note.find"}}]

    def test_a_typical_table_is_seen_whole(self):
        # Ten flat rows are well inside the budget: no preview, no read.
        mind = Harness([action(action="finish")]).assistant
        observation = mind._record("notebook", "notebook.note.find", {},
                                   {"notes": self.rows(10), "total": 10},
                                   "success")
        assert observation == {"status": "success", "result": {
            "notes": self.rows(10), "total": 10}}

    def test_a_huge_result_shows_as_many_whole_rows_as_fit(self):
        mind = Harness([action(action="finish")]).assistant
        rows = self.rows(400, size=200)
        observation = mind._record("notebook", "notebook.note.find", {},
                                   {"notes": rows, "total": 400}, "success")
        assert observation["truncated"] is True
        preview = observation["result_preview"]["notes"]
        assert preview["items_total"] == 400
        assert 20 < preview["items_shown"] < 400
        assert preview["items"] == rows[: preview["items_shown"]]

    def test_a_previewed_result_is_counted_not_blamed_on_the_question(self):
        """The note used to say only that the result was previewed, which
        left the model with nothing concrete — so people were told "the
        query is big", as though they had asked for too much. They had
        not: the FUNCTION returned more than fits. The note carries the
        counts now, and says not to blame the request."""
        mind = Harness([]).user("find the quotation thread").assistant
        rows = [{"body": "x" * 200, "subject": f"Re: chairs {i}"}
                for i in range(400)]
        observation = mind._record("outlook", "outlook.search.thread", {},
                                   {"messages": rows, "total": 400}, "success")
        note = observation["note"]
        shown = observation["result_preview"]["messages"]["items_shown"]
        assert f"messages: {shown} of 400" in note
        assert "never that their request was too large" in note
        assert len(json.dumps(observation)) <= Assistant.OBSERVATION_MAX_CHARS

    def test_a_wakeup_too_large_to_show_is_previewed_with_its_reference(self):
        """The clock wakes the mind with its fire's whole result, and an
        inbox check can bring more than a beat should see. The event is
        held to the same budget as an observation: a preview, the
        reference the fire stored, and the note that counts the rest."""
        mind = Harness([action(action="finish")]).assistant
        rows = [{"message_id": f"AAMk{i:04x}", "subject": f"Invoice {i}",
                 "snippet": "x" * 200} for i in range(400)]
        mind._absorb({"event": "wakeup", "schedule_id": "sch_1",
                      "function": "outlook.watch.new_mail",
                      "result": {"messages": rows, "checked": 1,
                                 "more": False, "storage_ref": "stg_mail"}})
        content = mind.state.messages[-1]["content"]
        head, body = content.split("\n", 1)
        assert head.startswith("EVENT wakeup at ")
        payload = json.loads(body)
        assert payload["schedule_id"] == "sch_1"
        assert payload["truncated"] is True
        assert payload["storage_ref"] == "stg_mail"
        assert "result" not in payload
        preview = payload["result_preview"]["messages"]
        assert preview["items_total"] == 400
        assert 10 < preview["items_shown"] < 400
        assert preview["items"] == rows[: preview["items_shown"]]
        assert f"messages: {preview['items_shown']} of 400" in payload["note"]
        assert len(body) <= Assistant.OBSERVATION_MAX_CHARS

        # A wakeup that fits is shown whole, exactly as before.
        mind._absorb({"event": "wakeup", "schedule_id": "sch_1",
                      "result": {"messages": rows[:3], "checked": 1}})
        payload = json.loads(mind.state.messages[-1]["content"].split("\n", 1)[1])
        assert payload["result"]["messages"] == rows[:3]
        assert "truncated" not in payload

    def test_a_long_list_is_read_in_pages_with_from(self):
        rows = self.rows(400, size=200)
        harness = Harness([action(action="finish")])
        harness.assistant.executor.resolver = None

        async def resolver(ref, path):
            return rows

        harness.assistant.executor.resolver = resolver
        first = run(harness.assistant._read({"storage_ref": "stg_1", "path": "notes"}))
        shown = first["result"]["value"]["items_shown"]
        assert first["result"]["value"]["items"] == rows[:shown]
        assert f'"from": {shown}' in first["result"]["note"]

        second = run(harness.assistant._read({
            "storage_ref": "stg_1", "path": "notes", "from": shown}))
        assert second["result"]["from"] == shown
        assert second["result"]["value"]["items"][0] == rows[shown]


class TestARefusedPart:
    def test_the_words_still_arrive_and_the_cycle_goes_on(self):
        """A platform that refuses a message's parts must not cost the
        user the words, and must never end the cycle silently."""
        harness = Harness([
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.note.save",
                   inputs={"notebook": "work", "title": "Ship it"}),
            action(action="say", text="Saved your note."),
            action(action="finish"),
        ])
        delivered = []

        async def refusing_sink(text, parts):
            if parts:
                raise RuntimeError("unknown part type 'success'")
            delivered.append((text, parts))

        harness.assistant.say_sink = refusing_sink
        run(harness.user("note down: ship it").assistant.run())
        assert delivered == [("Saved your note.", [])]
        # The model heard why, and the turn still finished.
        assert any("delivered without its data parts" in m["content"]
                   for m in harness.state.messages)
        assert json.loads(harness.state.messages[-1]["content"]) == {"action": "finish"}


class TestTheSkillsCatalog:
    """How many skills the frame lists is the chat's number: forty
    unless it says otherwise, zero for every one."""

    @staticmethod
    def _skills(count):
        return [{"ref": f"sk_{i}", "title": f"Skill {i}", "summary": "does a thing"}
                for i in range(count)]

    def test_the_standard_lists_forty_and_names_the_rest(self):
        harness = Harness([], skills=self._skills(45))
        block = harness.assistant._skills_block()
        assert harness.assistant.max_skills == 40
        assert "sk_39:" in block and "sk_40:" not in block
        assert "5 more skill(s) exist" in block

    def test_a_chat_raises_the_number_or_takes_the_valve_off(self):
        capped = Harness([], skills=self._skills(45), max_skills=2)
        block = capped.assistant._skills_block()
        assert "sk_1:" in block and "sk_2:" not in block
        assert "43 more skill(s) exist" in block

        unlimited = Harness([], skills=self._skills(45), max_skills=0)
        block = unlimited.assistant._skills_block()
        assert "sk_44:" in block
        assert "more skill(s)" not in block

    def test_the_number_moves_between_turns_and_the_frame_follows(self):
        harness = Harness([], skills=self._skills(45))
        harness.assistant.max_skills = 0
        harness.assistant.state.messages = [
            {"role": "system", "content": harness.assistant._system_prompt()}]
        harness.assistant.max_skills = 3
        harness.assistant.reframe()
        assert "3 more skill(s)" not in harness.assistant.state.messages[0]["content"]
        assert "42 more skill(s) exist" in harness.assistant.state.messages[0]["content"]


class TestRecall:
    """The archive is searched by words, newest first, and the frame
    says only that something has fallen out."""

    @staticmethod
    def _archived(harness):
        harness.assistant.state.summary = "DECISIONS AND FACTS\nThe venue is the Harbour hall.\n"
        harness.assistant.state.archive = [
            {"at": "2026-09-01 09:00", "section": "DECISIONS AND FACTS",
             "line": "The old venue was the Northlight hall."},
            {"at": "2026-09-10 14:00", "section": "DONE",
             "line": "Sent the Northlight deposit refund (inv_0042)."},
            {"at": "2026-09-12 08:00", "section": "OPEN THREADS",
             "line": "Dana has not confirmed the catering count."},
        ]
        return harness

    def test_recall_answers_with_matching_entries_newest_first(self):
        harness = self._archived(Harness([]))
        answer = harness.assistant._recall({"action": "recall", "query": "northlight"})
        assert answer["archived"] == 3 and answer["matched"] == 2
        assert [e["at"] for e in answer["entries"]] == ["2026-09-10 14:00", "2026-09-01 09:00"]
        everything = harness.assistant._recall({"action": "recall"})
        assert len(everything["entries"]) == 3

    def test_an_empty_archive_says_so(self):
        answer = Harness([]).assistant._recall({"action": "recall", "query": "venue"})
        assert answer["archived"] == 0 and "complete" in answer["note"]

    def test_the_frame_counts_what_fell_out_and_never_lists_it(self):
        harness = self._archived(Harness([]))
        frame = harness.assistant._system_prompt()
        assert "3 older line(s) have fallen out of this summary since 2026-09-01 09:00" in frame
        assert "Northlight" not in frame
        assert "The venue is the Harbour hall." in frame


def fake_agent(agent_id, name, description, tags=(), functions=(), examples=()):
    """An installed agent as the roster and the router see it."""
    from types import SimpleNamespace
    document = {
        "agent": {"id": agent_id, "name": name, "description": description,
                  "tags": list(tags),
                  "examples": [{"title": e, "prompt": e} for e in examples]},
        "tools": [{"id": "main", "functions": [
            {"id": f, "description": f"{f} things"} for f in functions]}],
    }
    return SimpleNamespace(
        digest=f"sha256:{agent_id}",
        manifest=SimpleNamespace(name=name, document=document, instructions=""))


EMBEDDING = {"provider": "fake", "model": "fake-embed", "api_key": "x", "responses": []}


def routing(**overrides):
    return {"threshold": 15, "shortlist": 15, "candidates": 50, "rerank": False,
            "open_max": 8, "embedding": EMBEDDING, **overrides}


class TestManyAgents:
    """More agents than the threshold: the open ones and the closest in
    meaning to the latest message are listed, the rest counted and
    searchable — and with no embedding model, every agent as before."""

    @staticmethod
    def _crowd(harness, count=30):
        agents = {}
        for i in range(count):
            agents[f"agt_{i:02d}"] = fake_agent(
                f"agt_{i:02d}", f"Widget {i}", f"tends widget number {i}",
                tags=["misc"], functions=["run"])
        agents["agt_mail"] = fake_agent(
            "agt_mail", "Outlook", "reads and sends mail",
            tags=["mail", "email"], functions=["send", "find"],
            examples=["send an email to Dana", "what mail arrived today"])
        agents["agt_inv"] = fake_agent(
            "agt_inv", "Invoicing", "issues invoices to customers",
            tags=["finance"], functions=["issue", "list"],
            examples=["issue an invoice for the March order"])
        harness.assistant.agents = agents
        return harness

    def _routed(self, tmp_path, text, **overrides):
        from ai_runtime.reasoning.agent_router import AgentRouter

        harness = self._crowd(Harness([], router=AgentRouter(tmp_path / "emb"),
                                      routing=routing(**overrides)))
        harness.assistant.post({"event": "user_message", "text": text})
        harness.assistant._drain()
        run(harness.assistant._route())
        return harness

    def test_a_small_roster_is_listed_whole(self):
        harness = Harness([])
        frame = harness.assistant._roster_block()
        assert "more agent(s)" not in frame
        assert all(agent_id in frame for agent_id in harness.assistant.agents)

    def test_a_crowd_is_shortlisted_by_meaning_and_counted(self, tmp_path):
        harness = self._routed(tmp_path, "send an email to Dana about the invoice")
        frame = harness.assistant._roster_block()
        assert frame.startswith("agt_mail — Outlook") or "agt_mail — Outlook" in frame
        assert "agt_inv — Invoicing" in frame
        assert "17 more agent(s) are installed but not listed" in frame
        assert "by meaning" in frame

    def test_an_open_agent_stays_listed_whatever_the_words(self, tmp_path):
        from ai_runtime.reasoning.agent_router import AgentRouter

        harness = self._crowd(Harness([], router=AgentRouter(tmp_path / "emb"),
                                      routing=routing()))
        harness.assistant.state.opened = ["agt_03"]
        harness.assistant.post({"event": "user_message", "text": "send mail"})
        harness.assistant._drain()
        run(harness.assistant._route())
        assert harness.assistant._roster_block().startswith("agt_03 — Widget 3")

    def test_without_an_embedding_model_every_agent_is_listed(self, tmp_path):
        harness = self._routed(tmp_path, "send mail", embedding=None)
        frame = harness.assistant._roster_block()
        assert "more agent(s)" not in frame
        assert frame.count("\n") == 31
        answer = run(harness.assistant._find_agents({"action": "find_agents", "query": "mail"}))
        assert "No embedding model" in answer["note"]

    def test_the_organizations_threshold_decides(self, tmp_path):
        harness = self._routed(tmp_path, "send mail", threshold=100)
        assert "more agent(s)" not in harness.assistant._roster_block()

    def test_find_agents_searches_the_whole_roster_by_meaning(self, tmp_path):
        harness = self._routed(tmp_path, "hello")
        answer = run(harness.assistant._find_agents({"action": "find_agents", "query": "issue an invoice"}))
        assert answer["installed"] == 32
        assert answer["agents"][0]["id"] == "agt_inv"
        assert answer["agents"][0]["closeness"] > 0


class TestTheOpenSet:
    """At most open_max agents stay open, least recently used out."""

    def test_opening_past_the_bound_closes_the_least_recently_used(self):
        harness = Harness([], routing={"open_max": 2})
        ids = sorted(harness.assistant.agents)[:3]
        assert len(ids) == 3, "the fixtures hold three agents"
        run(harness.assistant._open_agent({"agent": ids[0]}))
        run(harness.assistant._open_agent({"agent": ids[1]}))
        # ids[0] is used again, so ids[1] is the one to go.
        harness.assistant.state.touch_agent(ids[0])
        answer = run(harness.assistant._open_agent({"agent": ids[2]}))
        assert answer["closed"] == ids[1]
        assert harness.assistant.state.opened == [ids[0], ids[2]]
        assert "catalog" in answer

    def test_close_agent_frees_its_place(self):
        harness = Harness([])
        first = sorted(harness.assistant.agents)[0]
        run(harness.assistant._open_agent({"agent": first}))
        assert harness.assistant._close_agent({"agent": first}) == {"closed": first, "open": []}
        assert "not open" in harness.assistant._close_agent({"agent": first})["error"]
