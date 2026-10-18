"""save_skill — a skill made from the chat, when the person asks for one
(docs/system/assistant.md, save_skill).

The assistant never writes a skill. It offers one on a card that shows
it whole; the person's save creates it, as them, at the platform's door
(here the sim, standing in for AI:Approval:Decide). What these tests
pin: the card carries every word that would be kept; nothing is kept on
a decline, on silence, or without the grant to create skills; a saved
skill is listed under SKILLS at once and read by use_skill afterwards;
a helper cannot offer one.
"""

import asyncio
import json

from ai_runtime.mind.actions import ACTION_TOOLS
from ai_runtime.tests.test_assistant import Harness
from ai_runtime.tests.test_session import action, build, run
from contracts.chat import SKILL_BODY_MAX_CHARS
from ai_runtime.tests.sim.session_services import SimSessionServices

SCRIPT = "\n".join(
    ["```javascript",
     "const base = '/rest/api/2/issue';",
     *[f"await fetch(base, {{method: 'POST', body: JSON.stringify({{n: {n}}})}});"
       for n in range(60)],
     "```"])
BODY = ("Create and resolve Jira tickets in bulk through the REST API.\n\n"
        "1. Open the project's board in the browser.\n"
        "2. Run the script below in the page.\n"
        "3. Check every key came back 201.\n\n" + SCRIPT)
SKILL = {"title": "Jira bulk create and resolve",
         "summary": "When the user wants many Jira tickets created and resolved at once.",
         "body": BODY}


def offered(**overrides):
    return action(action="save_skill", **{**SKILL, **overrides})


def observations(session):
    return [json.loads(m["content"].split("\n", 1)[1])
            for m in session.assistant.state.messages
            if m["role"] == "user" and m["content"].startswith("OBSERVATION")]


def cards(services):
    return [e for e in services.events if e["event"] == "question_asked"]


async def turn(session, text="save that script as a skill"):
    await session.open()
    await session.deliver_user(text)
    await session.wait_idle()


class TestTheAction:
    def test_it_is_offered_as_a_tool_with_the_three_fields(self):
        [tool] = [t for t in ACTION_TOOLS if t["function"]["name"] == "save_skill"]
        parameters = tool["function"]["parameters"]
        assert set(parameters["required"]) == {"title", "summary", "body"}
        assert parameters["additionalProperties"] is False
        assert "own words" in tool["function"]["description"]

    def test_a_skill_past_the_stores_bounds_is_refused_before_any_card(self):
        asked = []

        async def save_skill(skill):
            asked.append(skill)
            return {"status": "saved"}

        harness = Harness([
            offered(body="x" * (SKILL_BODY_MAX_CHARS + 1)),
            offered(summary="   "),
            action(action="finish"),
        ], save_skill=save_skill)
        run(harness.user("save it").assistant.run())
        errors = [json.loads(m["content"].split("\n", 1)[1])["error"]
                  for m in harness.state.messages
                  if m["content"].startswith("OBSERVATION")]
        assert len(errors) == 2 and all("save_skill needs" in e for e in errors)
        assert str(SKILL_BODY_MAX_CHARS) in errors[0]
        assert asked == []

    def test_a_helper_cannot_offer_one(self):
        async def finish(summary, reason):
            pass

        harness = Harness([offered(), action(action="finish")], finish=finish)
        run(harness.user("save it").assistant.run())
        observation = json.loads(
            harness.state.messages[3]["content"].split("\n", 1)[1])
        assert "not a helper's" in observation["error"]


class TestTheCard:
    def test_the_card_shows_the_skill_whole_and_a_decline_keeps_nothing(self):
        asked = []

        async def decider(request):
            asked.append(request)
            return "decline"

        services = SimSessionServices(decider=decider)
        session, _ = build([offered(), action(action="finish")], services)
        run(turn(session))

        [card] = cards(services)
        assert card["expects"] == "skill" and card["function"] == "save_skill"
        # Every word that would be kept is on the card: nothing cut, the
        # script whole.
        assert card["skill"] == SKILL
        assert card["skill"]["body"].count("await fetch") == 60
        assert "Jira bulk create and resolve" in card["question"]
        assert asked[0]["skill"] == SKILL
        assert services.skills_created == [] and services.skills_rows == []
        [observation] = observations(session)
        assert observation["saved"] is False
        assert "chose not to save" in observation["note"]

    def test_nobody_answering_keeps_nothing(self):
        session, services = build([offered(), action(action="finish")])

        async def scenario():
            await session.open()
            session.cards.QUESTION_WAIT_SECONDS = 0.05
            await session.deliver_user("save that as a skill")
            await session.wait_idle()

        run(scenario())
        assert len(cards(services)) == 1
        assert any(e["event"] == "question_closed" and e["status"] == "expired"
                   for e in services.events)
        assert services.skills_created == [] and services.skills_rows == []
        [observation] = observations(session)
        assert observation["saved"] is False and "Nobody answered" in observation["note"]

    def test_without_the_grant_no_card_is_shown_and_the_user_is_told(self):
        async def decider(request):
            raise AssertionError("the person was asked")

        services = SimSessionServices(decider=decider)
        services.may_create_skills = False
        session, _ = build([offered(), action(action="finish")], services)
        run(turn(session))
        assert cards(services) == [] and services.approvals == {}
        assert services.skills_created == []
        [observation] = observations(session)
        assert "skills:skill:create" in observation["error"]
        assert "Create skills" in observation["note"]

    def test_a_title_already_listed_is_refused_before_any_card(self):
        services = SimSessionServices()
        services.skills_rows = [{"ref": "sk_9", "title": "jira BULK create and resolve",
                                 "summary": "older"}]
        session, _ = build([offered(), action(action="finish")], services)
        run(turn(session))
        assert services.approvals == {}
        [observation] = observations(session)
        assert "sk_9" in observation["error"] and "distinct title" in observation["error"]


class TestSaved:
    def test_a_save_creates_it_from_the_card_and_it_is_listed_and_read(self):
        async def decider(request):
            return "save"

        services = SimSessionServices(decider=decider)
        session, _ = build([
            offered(), action(action="say", text="Saved.", final=True),
        ], services)
        run(turn(session))

        [created] = services.skills_created
        assert created["chat_id"] == "chat_1"
        assert {k: created[k] for k in SKILL} == SKILL
        ref = created["ref"]
        [observation] = observations(session)
        assert observation["saved"] is True and observation["skill"] == ref
        # Listed under SKILLS at once: the very next call already read it.
        assert f"{ref}: Jira bulk create and resolve" in \
            session.assistant.connector.calls[1]["messages"][0]["content"]

        # And the next turn reads it back whole.
        session.connector.responses = [
            action(action="use_skill", skill=ref),
            action(action="say", text="Running it.", final=True),
        ]

        async def again():
            await session.deliver_user("do the jira thing again")
            await session.wait_idle()

        run(again())
        read = observations(session)[-1]
        assert read["skill"] == ref and "await fetch" in read["text"]

    def test_a_save_through_the_door_reaches_a_narrowed_chat_too(self):
        """The person answers later, on the page: the backend saves it
        and the door carries the row; a chat narrowed to some skills
        keeps the one just saved in it."""
        services = SimSessionServices()
        services.skills_rows = [{"ref": "sk_1", "title": "Style guide",
                                 "summary": "how we write"}]
        session, _ = build([offered(), action(action="finish")], services,
                           skills=["sk_1"])

        async def scenario():
            await session.open()
            await session.deliver_user("save that script as a skill")
            deadline = asyncio.get_running_loop().time() + 5
            while not cards(services):
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.02)
            approval_id = cards(services)[0]["approval_id"]
            carried = services.decide_skill(approval_id, "save")
            await session.deliver_answer(approval_id, carried)
            await session.wait_idle()
            return carried

        carried = run(scenario())
        ref = carried["skill"]["ref"]
        assert ref in session.enabled_skills
        frame = session.assistant._system_prompt()
        assert f"{ref}: Jira bulk create and resolve" in frame
        assert "sk_1: Style guide" in frame
        # A contract that now names both changes nothing.
        session.adopt(session.roster, session.chat_level, skills=["sk_1", ref])
        assert f"{ref}:" in session.assistant._system_prompt()
