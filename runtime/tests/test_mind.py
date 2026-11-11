"""The mind, on a real chat with the API server, the sandbox and the model faked (mind.md)."""

import asyncio
from types import SimpleNamespace

from fakes import AGENT, call, function, held, message, said, settled, until
from runtime.mind.history import History
from runtime.mind.tools import Tools


async def slow(world, process, call_id, inputs):
    await asyncio.sleep(0.3)
    return {"slow": "done"}, None


async def endless(world, process, call_id, inputs):
    await asyncio.sleep(60)


async def test_its_words_are_said_and_its_state_kept():
    api, sandboxes, chat = await held(["Hi there."])
    await chat.told(message(1, "hello"))
    await settled(chat)
    assert said(api) == ["Hi there."]
    assert [(data["event"], data["data"]) for data in api.of("AI:Event:append")] == [("working", {}), ("idle", {})]
    # The words as they were written pass through the chat, for the API servers that ask, and are never kept.
    assert [(event["event"], event["data"]) for _, event in chat.passing.recent] == [("saying", {"text": "Hi there."})]
    assert api.of("AI:State:save")[-1]["state"]["taken"] == [1]
    request = chat._connector.requests[0]
    assert "## Probe (probe)" in request.system_stable and "Tidy: How to tidy." in request.system_rest
    assert request.messages[-1]["text"].startswith("[") and request.messages[-1]["text"].endswith("UTC] hello")
    assert {"probe__report", "task", "ask", "skill"} <= {tool["name"] for tool in request.tools}


async def test_a_call_is_handed_the_files_it_names_and_the_files_it_makes_are_shown():
    async def made(world, process, call_id, inputs):
        return {"report": "out/r.txt"}, [{"id": "x1", "path": "out/r.txt"}]

    api, sandboxes, chat = await held([call(("probe__report", {"file": "f1"})), "Here is the report."])
    sandboxes.behave["report"] = made
    await chat.told(message(1, "check this", files=("f1",)))
    await settled(chat)
    sent = sandboxes.sent[0]
    assert sent["body"]["data"]["inputs"] == {"file": "in/f1/notes.txt"} and sent["files"][0]["id"] == "f1"
    assert api.of("AI:Message:create")[0]["files"] == ["f-made"]
    answer = chat._connector.requests[1].messages[-1]
    assert answer["role"] == "tool" and '"report": "f-made"' in answer["text"]
    assert [data["data"]["status"] for data in api.of("AI:Event:append") if data["event"] == "work"] == ["running", "done"]


async def test_a_task_reports_its_last_words():
    api, sandboxes, chat = await held([call(("task", {"brief": "Count to three."})), "One, two, three.", "It counted."])
    await chat.told(message(1, "count"))
    await settled(chat)
    asked = chat._connector.requests
    assert "You are a task" in asked[1].system_rest and asked[1].messages == [{"role": "user", "text": "Count to three."}]
    assert "ask" not in {tool["name"] for tool in asked[1].tools}
    assert asked[2].messages[-1]["text"] == "One, two, three."
    assert said(api) == ["It counted."] and list(chat.mind.brains) == [""]


async def test_a_new_message_leaves_a_slow_call_running_and_its_answer_comes_later():
    api, sandboxes, chat = await held([call(("probe__slow", {})), "Still on it.", "The slow one is done."])
    sandboxes.behave["slow"] = slow
    await chat.told(message(1, "go slow"))
    await until(lambda: sandboxes.running)
    await chat.told(message(2, "anything else?"))
    await settled(chat)
    second, third = chat._connector.requests[1].messages, chat._connector.requests[2].messages
    assert second[-2]["role"] == "tool" and second[-2]["text"].startswith("Still running, as c1")
    assert third[-1]["text"].startswith("c1 (Probe · Slow) answered:")
    assert said(api) == ["Still on it.", "The slow one is done."]


async def test_a_task_gives_words_to_the_one_that_started_it():
    api, sandboxes, chat = await held([call(("task", {"brief": "Count."})), call(("tell", {"to": "parent",
                                                                                         "text": "Halfway."})),
                                       "Done counting.", "It is halfway, then done."])
    await chat.told(message(1, "count"))
    await settled(chat)
    last = [m["text"] for m in chat._connector.requests[3].messages if m["role"] == "user"]
    assert last[-2:] == ["t1 says: Halfway.", "t1 (Task · Count.) answered: Done counting."]
    assert said(api) == ["It is halfway, then done."]


async def test_the_model_sees_the_pictures_attached():
    api, sandboxes, chat = await held(["A red square."])
    api.answers["Files:Piece:read"] = {"data": b"PNG", "size": 3, "name": "square.png", "type": "image/png"}
    await chat.told(message(1, "what is this?", files=("p1",)))
    await settled(chat)
    assert chat._connector.requests[0].messages[-1]["images"] == [{"mime": "image/png", "data": "UE5H"}]


async def test_messages_out_of_order_are_each_taken_once():
    api, sandboxes, chat = await held(["Got three.", "Got two."])
    await chat.told(message(3, "three"))
    await settled(chat)
    await chat.told(message(2, "two"))
    await settled(chat)
    await chat.told(message(2, "two"))
    await chat.told(message(3, "three"))
    await settled(chat)
    assert said(api) == ["Got three.", "Got two."] and api.of("AI:State:save")[-1]["state"]["taken"] == [2, 3]


async def test_a_card_given_up_on_expires():
    api, sandboxes, chat = await held([call(("probe__wipe", {})), call(("stop", {"id": "c1"})), "Stopped it."])
    await chat.told(message(1, "wipe it"))
    await until(lambda: api.of("AI:Approval:open"))
    await chat.told(message(2, "no, leave it"))
    await settled(chat)
    await until(lambda: api.of("AI:Approval:expire"))
    assert api.of("AI:Approval:expire") == [{"chat_id": "chat1", "card": "card1"}] and said(api) == ["Stopped it."]


async def test_a_question_waits_for_the_answer():
    api, sandboxes, chat = await held([call(("ask", {"text": "Which?", "choices": ["red", "blue"]})), "Blue it is."])
    await chat.told(message(1, "pick for me"))
    card = (await until(lambda: api.of("AI:Approval:open")))[0]
    assert card["kind"] == "question" and card["request"] == {"text": "Which?", "expects": "choice",
                                                             "choices": ["red", "blue"]}
    await chat.told({"type": "answer", "card": "card1", "answer": {"choice": "blue"}})
    await settled(chat)
    assert chat._connector.requests[1].messages[-1]["text"] == "blue"


async def test_with_nobody_there_nothing_is_asked_and_the_run_is_told():
    api, sandboxes, chat = await held([call(("ask", {"text": "Sure?"}), ("probe__wipe", {})), "Done, unasked."])
    await chat.told({"type": "schedule", "schedule_id": "s9", "what": {"prompt": "Tidy up."}})
    await settled(chat)
    answers = [m["text"] for m in chat._connector.requests[1].messages if m["role"] == "tool"]
    assert answers[0].startswith("Not done: nobody is here") and answers[1] == "refused: Nobody is there to approve this."
    assert not api.of("AI:Approval:open")
    assert api.of("AI:Schedule:ran") == [{"chat_id": "chat1", "schedule_id": "s9", "failed": False,
                                          "summary": "Done, unasked."}]


async def test_a_stop_cancels_the_call_in_the_program_and_thinks_no_more():
    api, sandboxes, chat = await held([call(("probe__slow", {}))])
    sandboxes.behave["slow"] = endless
    await chat.told(message(1, "go"))
    await until(lambda: sandboxes.running)
    await chat.told({"type": "stop"})
    await settled(chat)
    await until(lambda: any(m["body"]["type"] == "cancel" for m in sandboxes.sent))
    assert chat.mind.brains[""].work["c1"]["status"] == "stopped" and len(chat._connector.requests) == 1


async def test_it_is_read_back_with_no_model_and_takes_a_message_once():
    api, sandboxes, chat = await held(["First answer."])
    await chat.told(message(1, "hello"))
    await settled(chat)
    state = api.of("AI:State:save")[-1]["state"]
    _, _, again = await held([], loaded={"messages": [message(1, "hello")["message"]], "cards": [], "state": state})
    await again.told(message(1, "hello"))
    await asyncio.sleep(0.1)
    assert again._connector.requests == [] and again.mind.idle()
    assert again.mind.brains[""].history.entries == chat.mind.brains[""].history.entries


async def test_a_restart_fails_the_calls_in_flight_and_thinks_again():
    api, sandboxes, chat = await held([call(("probe__slow", {}))])
    sandboxes.behave["slow"] = endless
    await chat.told(message(1, "go"))
    await until(lambda: sandboxes.running and chat.mind.sending.done())
    state = api.of("AI:State:save")[-1]["state"]
    api2, _, again = await held(["It was cut short."], loaded={"messages": [], "cards": [], "state": state})
    await settled(again)
    assert again._connector.requests[0].messages[-1]["text"] == "failed: the runtime restarted before it ended."
    assert said(api2) == ["It was cut short."]


async def test_a_long_history_is_folded_into_a_chapter(monkeypatch):
    monkeypatch.setattr(History, "VIEW_MOST", 400)
    api, sandboxes, chat = await held(["A" * 300, "They said hello.", "Hello again."])
    await chat.told(message(1, "hello"))
    await settled(chat)
    await chat.told(message(2, "hello again"))
    await settled(chat)
    asked = chat._connector.requests
    assert asked[1].system_rest.startswith("You are given part of a conversation") and not asked[1].tools
    assert asked[2].messages[0]["text"] == "Earlier, folded as ch1: They said hello."
    assert said(api) == ["A" * 300, "Hello again."]


async def test_the_turn_budget_ends_a_turn():
    api, sandboxes, chat = await held([call(("look", {})), call(("look", {}))], turn_budget=2)
    await chat.told(message(1, "look around"))
    await settled(chat)
    assert said(api) == ["Stopped after 2 steps with no word from the person."]


def test_past_a_hundred_functions_an_agent_is_opened():
    many = {**AGENT, "agent_id": "many.agent", "manifest": {**AGENT["manifest"], "agent": {"name": "Many"},
                                                            "functions": [function(f"f{n}") for n in range(120)]}}
    chat = SimpleNamespace(agents={"probe": AGENT, "many.agent": many})
    brain = SimpleNamespace(id="", depth=0, opened=[], mind=SimpleNamespace(chat=chat, mcp_tools=None))
    names = [tool["name"] for tool in Tools(brain).specs]
    assert "open" in names and not any(name.startswith("many") for name in names)
    brain.opened = ["many.agent"]
    names = [tool["name"] for tool in Tools(brain).specs]
    assert sum(name.startswith("many_agent__") for name in names) == 100 and len(set(names)) == len(names)
    task = SimpleNamespace(id="t1", depth=2, opened=[], mind=brain.mind)
    assert {"tell", "look", "stop"} <= {tool["name"] for tool in Tools(task).specs}
    assert not {"task", "ask", "schedule"} & {tool["name"] for tool in Tools(task).specs}


async def test_the_person_stops_one_call_and_the_model_is_told():
    api, sandboxes, chat = await held([call(("probe__slow", {})), "Stopped, as you asked."])
    sandboxes.behave["slow"] = endless
    await chat.told(message(1, "go"))
    await until(lambda: sandboxes.running)
    await chat.told({"type": "stop", "work": "c1"})
    await settled(chat)
    await until(lambda: any(m["body"]["type"] == "cancel" for m in sandboxes.sent))
    assert chat._connector.requests[1].messages[-1]["text"] == "Stopped by the person."
    assert said(api) == ["Stopped, as you asked."] and not api.of("AI:Approval:expire")
