"""The assistant through a real chat, on the stand-ins of fakes.py: what it says and keeps, one card per step, runs at
their origin's trust, work that waits on the person across a restart, and what a program asks and reaches within its
call (assistant.md; runtime.md §5–§8)."""

import asyncio
import hashlib
import json

from fakes import calls, message, placed, said, settled, until, work
from runtime.assistant.assistant import Assistant


def sent(engine):
    """The inputs of every call sent to a program."""
    return [m["body"]["data"]["inputs"] for m in engine.sandboxes.sent if m["body"]["type"] == "call"]


def answer(card, value):
    return {"type": "answer", "card": card, "answer": value}


async def opened(api, kind="approval"):
    """The card of this kind the chat opened, once it has."""
    return await until(lambda: next((data for data in api.of("AI:Approval:open") if data["kind"] == kind), None))


async def test_its_words_are_said_and_an_output_is_kept_whole_and_shown_in_part():
    api, engine, chat = await placed([calls(("probe__report", {})), "All done."])

    async def long(program, inputs):
        return {"text": "x" * 20_000}

    engine.sandboxes.behave["report"] = long
    chat.put(message(1, "Check it"))
    await until(lambda: len(said(api)) == 2)
    await settled(chat)
    whole = json.dumps({"text": "x" * 20_000})
    assert said(api) == ["Doing it.", "All done."] and list(api.outputs.values()) == [whole]
    assert "run" not in api.of("AI:Message:create")[0]  # the chat's own words are no run's
    entry = work(chat)[0]
    assert entry["status"] == "done" and entry["output"] == {"size": len(whole), "line": whole[:200],
                                                             "ref": hashlib.sha256(whole.encode()).hexdigest()}
    shown = next(m for m in chat._connector.requests[-1].messages if m["role"] == "tool")["text"]
    assert len(shown) < 16_100 and shown.endswith("look at c1 from 16000.")
    # what a thought was about to do was kept before any of it ran
    assert any(entry["status"] == "running" for save in api.of("AI:State:save")
               for entry in save["parts"].get("work:0") or [])


async def test_a_steps_calls_are_one_card_answered_item_by_item():
    api, engine, chat = await placed([calls(("probe__wipe", {"what": "a"}), ("probe__wipe", {"what": "b"})),
                                      "Wiped a."])
    chat.put(message(1, "Wipe a and b"))
    card = await opened(api)
    assert [item["id"] for item in card["request"]["items"]] == ["c1", "c2"] and card["request"]["why"] == "Doing it."
    await until(lambda: [e["status"] for e in work(chat)] == ["waiting", "waiting"])
    chat.put(answer("card1", {"items": {"c1": True, "c2": False}}))
    await until(lambda: "Wiped a." in said(api))
    await settled(chat)
    assert {e["id"]: e["status"] for e in work(chat)} == {"c1": "done", "c2": "failed"}
    assert len(api.of("AI:Approval:open")) == 1
    assert sent(engine) == [{"what": "a"}]


async def test_a_schedules_run_works_at_its_trust_and_its_words_are_posted():
    api, engine, chat = await placed([calls(("probe__wipe", {"what": "a"})), "Wiped a."])
    chat.put({"type": "schedule", "schedule_id": "s1", "what": {"prompt": "Wipe a"}, "trust": 3})
    await until(lambda: api.of("AI:Schedule:ran"))
    await settled(chat)
    assert api.of("AI:Approval:open") == [] and said(api) == ["Wiped a."]
    assert api.of("AI:Schedule:ran") == [{"chat_id": "chat1", "schedule_id": "s1", "failed": False,
                                          "summary": "Wiped a."}]
    assert [(e["origin"], e["status"]) for e in work(chat)] == [("schedule:s1", "done")]
    assert "A run (schedule:s1) said: Wiped a." in chat.mind.threads[""].history.entries[-1]["text"]
    # the page is told whose run its message and its work are, and the work's whole story but its inputs whole
    run = {"thread": "r1", "origin": "schedule:s1", "title": "Wipe a", "message": 0}
    assert api.of("AI:Message:create")[0]["run"] == run
    told = [data["data"] for data in api.of("AI:Event:append") if data["event"] == "work"]
    assert told[-1]["run"] == run and told[-1]["means"] == {"agent": "probe", "function": "wipe"}
    assert told[-1]["why"] == "Doing it." and told[-1]["output"]["ref"] and told[-1]["inputs"] == '{"what": "a"}'


async def test_a_run_below_what_it_needs_asks_its_person_and_waits():
    api, engine, chat = await placed([calls(("probe__wipe", {"what": "a"})), "Wiped a."])
    chat.put({"type": "schedule", "schedule_id": "s1", "what": {"prompt": "Wipe a"}, "trust": 0})
    await opened(api)
    entry = await until(lambda: work(chat) and work(chat)[0]["status"] == "waiting" and work(chat)[0])
    assert chat.mind.idle()  # waiting only for the person: the chat may be let go meanwhile
    chat.put(answer("card1", {"items": {entry["id"]: True}}))
    await until(lambda: api.of("AI:Schedule:ran"))
    assert api.of("AI:Schedule:ran")[0]["failed"] is False and work(chat)[0]["status"] == "done"


async def test_work_waiting_on_a_card_goes_on_after_a_restart_with_the_answer_given_meanwhile():
    api, _, chat = await placed([calls(("probe__wipe", {"what": "a"}))])
    chat.put(message(1, "Wipe a"))
    await opened(api)
    await until(lambda: work(chat) and work(chat)[0]["status"] == "waiting")
    await chat.let_go()
    state = Assistant.assembled(api.parts)
    assert (state["work"][0]["status"], state["work"][0]["card"]) == ("waiting", "card1")

    loaded = {"messages": [], "state": {"parts": dict(api.parts)},
              "cards": [{"card_id": "card1", "status": "answered", "answer": {"items": {"c1": True}}}]}
    api, engine, chat = await placed(["Wiped."], loaded)
    await until(lambda: said(api))
    await settled(chat)
    assert api.of("AI:Approval:open") == [] and said(api) == ["Wiped."] and work(chat)[0]["status"] == "done"
    assert sent(engine) == [{"what": "a"}]


async def test_stopping_work_that_waits_on_a_card_expires_the_card():
    api, _, chat = await placed([calls(("probe__wipe", {"what": "a"})), "Stopped it."])
    chat.put(message(1, "Wipe a"))
    await opened(api)
    await until(lambda: work(chat) and work(chat)[0]["status"] == "waiting")
    chat.put({"type": "stop", "work": "c1"})
    await until(lambda: "Stopped it." in said(api))
    await settled(chat)
    assert work(chat)[0]["status"] == "stopped" and api.of("AI:Approval:expire") == [{"chat_id": "chat1",
                                                                                      "card": "card1"}]


async def test_a_programs_question_within_its_call_is_a_card_whose_answer_it_gets():
    api, engine, chat = await placed([calls(("probe__report", {})), "It picked the blue one."])

    async def asks(program, inputs):
        answered = await program.ask("chat", {"text": "Which?", "expects": "text"})
        return {"picked": answered["data"]["output"]["answer"]}

    engine.sandboxes.behave["report"] = asks
    chat.put(message(1, "Check it"))
    card = await opened(api, "question")
    assert card["request"] == {"text": "Which?", "expects": "text"} and card["agent"] == "probe" and card["call"]
    await until(lambda: work(chat) and work(chat)[0]["status"] == "waiting")
    chat.put(answer("card1", {"text": "the blue one"}))
    await until(lambda: len(said(api)) == 2)
    await settled(chat)
    assert work(chat)[0]["status"] == "done" and "the blue one" in work(chat)[0]["output"]["line"]


async def test_what_a_program_reaches_declared_revealed_or_opened_for_its_call_and_never_blocked():
    api, engine, chat = await placed([calls(("probe__report", {})), "Done."])
    api.answers["Records:Record:read"] = {"ref": "s1", "fields": {"server": "Mail.Example.org"}}
    seen = {}

    async def reaches(program, inputs):
        def reach(host, port=443):
            return chat.programs.reach(program.process, "tcp", host, port)

        seen["before"] = [reach("example.com"), reach("example.com", 22), reach("blocked.example.com"),
                          reach("mail.example.org"), reach("api.other.com")]
        await program.ask("record.read", {"resource": "mailbox"})
        await program.ask("network.open", {"hosts": ["API.other.com"], "purpose": "To fetch."})
        seen["after"] = [reach("mail.example.org"), reach("api.other.com"), reach("api.other.com", 8080)]
        return {}

    engine.sandboxes.behave["report"] = reaches
    chat.put(message(1, "Check it"))
    card = await opened(api, "network")
    assert card["request"]["hosts"] == ["api.other.com"]
    chat.put(answer("card1", {"allowed": True}))
    await until(lambda: len(said(api)) == 2)
    await settled(chat)
    before, after = seen["before"], seen["after"]
    assert before[0] is None and "blocked by the organization's policy" in before[2]
    assert all(isinstance(reason, str) for reason in (before[1], before[3], before[4], after[2]))
    assert after[0] is None and isinstance(after[1], asyncio.Future) and after[1].done()  # closed with its call
    assert work(chat)[0]["records"] == [{"operation": "read", "resource": "mailbox", "ref": "s1"}]


def test_a_state_is_cut_into_parts_and_put_back_together():
    state = {"count": 3, "taken": [1], "known": [], "files": [], "work": [{"id": f"c{n}"} for n in range(120)],
             "threads": [{"id": "", "kind": "chat", "entries": [{"kind": "in", "text": str(n)} for n in range(60)]}]}
    parts = Assistant.parts(state)
    assert sorted(parts) == ["head", "history::0", "history::1", "thread:", "work:0", "work:1", "work:2"]
    assert Assistant.assembled(parts) == state


async def test_a_save_sends_only_the_parts_that_changed():
    api, _, chat = await placed([])
    for change in (None, None, "count"):
        if change:
            chat.mind.count += 1
        await chat.mind._keep()
    assert [(save.get("whole", False), sorted(save["parts"])) for save in api.of("AI:State:save")] == [
        (True, ["head", "thread:"]), (False, ["head"])]
