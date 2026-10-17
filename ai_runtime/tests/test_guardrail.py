"""The organization's own rules (Settings:Guardrail, docs/system/safety.md).

What is pinned: the text from the contract is written into the frame
after the instructions and before what changes turn to turn; an edit
reaches the chat already open at its next turn; a helper is told the
same rules; the code reviewer reads them; and an organization that
wrote none is told so in one word.
"""


from ai_runtime.chat.code_review import CodeReviewer
from ai_runtime.door.platform import Backend
from ai_runtime.llms.connector.fake import FakeConnector
from ai_runtime.mind import frame
from ai_runtime.mind.state import AssistantState
from ai_runtime.tests.test_code_review import CODE
from ai_runtime.tests.test_platform import StubGateway
from ai_runtime.tests.test_session import action, build, run
from ai_runtime.tests.test_session_door import build as build_host
from ai_runtime.tests.test_sub_assistants import child_of

RULES = "Never write to the Finance notebook.\nAnswer in French."
HEADING = "ORGANIZATION'S OWN RULES"


def block_of(prompt: str) -> str:
    """What stands between the rules' heading and the next block."""
    start = prompt.index(HEADING)
    return prompt[start:prompt.index("\nPLAN\n", start)]


class TestTheFrame:
    def test_the_rules_follow_the_instructions_and_precede_what_moves(self):
        prompt = frame.system_prompt(AssistantState(), {}, None, [], 40, [],
                                     guardrail=RULES)
        at = prompt.index(RULES)
        # After every instruction the platform gives...
        assert prompt.index("A standing instruction") < prompt.index(HEADING) < at
        # ...and before the blocks that change from turn to turn, so the
        # part of the frame a provider caches is the same every turn.
        for volatile in ("\nPLAN\n", "\nCONVERSATION SO FAR\n", "\nAGENTS\n",
                         "\nSKILLS\n", "\nMEMORY\n"):
            assert at < prompt.index(volatile)
        assert "never widen it" in block_of(prompt)

    def test_no_rules_is_one_word(self):
        prompt = frame.system_prompt(AssistantState(), {}, None, [], 40, [])
        assert block_of(prompt).rstrip().endswith("(none)")
        assert frame.guardrail_block("  \n ") == "(none)"

    def test_the_text_is_text_and_opens_no_blank(self):
        prompt = frame.system_prompt(AssistantState(), {}, None, [], 40,
                                     ["kept"], guardrail="Mind the {memories}.")
        assert "Mind the {memories}." in prompt


class TestTheSession:
    def test_the_contracts_rules_are_in_the_frame_the_mind_thinks_under(self):
        session, _ = build([action(action="finish")], guardrail=RULES)
        run(session.open())
        assert RULES in session.assistant._system_prompt()

    def test_an_edit_reaches_the_next_turn(self):
        session, _ = build([action(action="finish")], guardrail=RULES)
        run(session.open())
        session.adopt(session.roster, session.chat_level,
                      guardrail="Only English.")
        system = session.assistant._system_prompt()
        assert "Only English." in system and RULES not in system
        assert session.cards.reviewer.guardrail == "Only English."
        # Cleared: the frame says there are none.
        session.adopt(session.roster, session.chat_level, guardrail="")
        assert block_of(session.assistant._system_prompt()) \
            .rstrip().endswith("(none)")

    def test_a_turn_that_says_nothing_of_the_rules_keeps_them(self):
        session, _ = build([action(action="finish")], guardrail=RULES)
        run(session.open())
        session.adopt(session.roster, session.chat_level)
        assert RULES in session.assistant._system_prompt()

    def test_the_door_carries_the_rules_and_rereads_them_each_turn(self):
        host, services = build_host([action(action="finish"),
                                     action(action="finish")])
        services.contracts["chat_1"]["guardrail"] = RULES

        async def scenario():
            await host.handle("chat_1", {"event": "user_message", "text": "one"})
            session = host.sessions["chat_1"]
            await session.wait_idle()
            first = session.assistant._system_prompt()
            # An administrator edited them on the Safety page.
            services.contracts["chat_1"]["guardrail"] = "Only English."
            await host.handle("chat_1", {"event": "user_message", "text": "two"})
            await session.wait_idle()
            return first, session.assistant._system_prompt()

        first, second = run(scenario())
        assert RULES in first
        assert "Only English." in second and RULES not in second


class TestAHelper:
    def test_a_sub_assistant_is_told_the_same_rules(self):
        session, services = build([
            # parent
            action(action="spawn", goal="Say hello.", agents=[]),
            action(action="finish"),
            # child
            action(action="finish", summary="Said hello."),
            # parent, on the report
            action(action="finish"),
        ], guardrail=RULES)

        async def scenario():
            await session.open()
            await session.deliver_user("get a helper to say hello")
            await session.wait_idle()

        run(scenario())
        child_id = f"chat_1/{child_of(session).child}"
        system = services.states[child_id]["messages"][0]
        assert system["role"] == "system" and RULES in system["content"]


class TestTheReviewer:
    def test_the_reviewer_reads_the_rules(self):
        connector = FakeConnector(['{"verdict": "agrees", "note": "Fine."}'])
        run(CodeReviewer(connector, guardrail=RULES).review(CODE))
        system = connector.calls[0]["messages"][0]["content"]
        assert HEADING in system and RULES in system
        assert "data" in system   # the instructions it had are still there

    def test_a_reviewer_with_no_rules_is_told_none(self):
        connector = FakeConnector(['{"verdict": "agrees", "note": "Fine."}'])
        run(CodeReviewer(connector).review(CODE))
        system = connector.calls[0]["messages"][0]["content"]
        assert system.rstrip().endswith("(none)")
        assert "{guardrail}" not in system


class TestThePlatform:
    def test_the_contract_carries_text_and_only_text(self):
        for said, kept in ((RULES, RULES), (None, ""), ({"text": "x"}, "")):
            backend = Backend(StubGateway({"AI:Chat:Contract": {
                "agents": [], "llm": None, "guardrail": said}}))
            backend.grant("chat_1", "tok-1")
            assert run(backend.contract("chat_1"))["guardrail"] == kept

    def test_no_text_in_the_title_summary_or_rerank_prompts(self):
        # The rules are for what the assistant does and what the
        # reviewer judges; the housekeeping prompts are not told them.
        from ai_runtime.prompts import Prompts
        for name in ("summary", "rerank", "rerank_ask", "code_review_ask"):
            assert "{guardrail}" not in Prompts.text(name)
