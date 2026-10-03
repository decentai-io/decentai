"""What a person may choose about how a chat thinks.

Five settings, three layers: what the platform ships, what this person
chose on their preferences page, and what a single chat says. The last
one with an answer wins, and none of it comes from the environment.

There is no deployment ceiling. What a chat or a schedule may do without
being asked is chosen by whoever is having the conversation or wrote the
schedule, and three things already bound that: entitlement decides which
functions exist for them at all, the autonomous-schedules grant decides
who may let standing work act above the standard level, and the
invocation gate decides what stops to ask. That the grant still gates a
schedules are gone with the workflows domain.
"""

from conftest import app_call, define_secret
from test_runtime_secret_use import make_llm_connection, signing_key  # noqa: F401


class TestTheShapeOfALevel:
    """0 to 3 and nothing else — the one rule left about a level."""

    def test_a_level_in_range_passes(self, app):
        from api.services.chat_session.settings import TrustLevel

        for level in (0, 1, 2, 3):
            assert TrustLevel().check({}, level) == (level, "")

    def test_out_of_range_is_refused(self, app):
        from api.services.chat_session.settings import TrustLevel

        for level in (4, -1, 99):
            value, problem = TrustLevel().check({}, level)
            assert value is None
            assert "0 to 3" in problem

    def test_nonsense_is_refused_rather_than_guessed(self, app):
        from api.services.chat_session.settings import TrustLevel

        for level in ("high", None, [3]):
            assert TrustLevel().check({}, level)[0] is None, level

    def test_a_stored_level_is_made_safe_not_refused(self, app):
        """Clamp is for a level decided earlier, with nobody present to
        tell. A real level is honoured; anything else becomes standard."""
        from api.services.chat_session.settings import TrustLevel

        level = TrustLevel()
        for real in (0, 1, 2, 3):
            assert level.clamp(real) == real

        for damaged in (9, -5, "nonsense", None, [3]):
            assert level.clamp(damaged) == level.STANDARD, damaged

        assert level.STANDARD < level.HIGHEST, (
            "an unreadable level must not become the most permissive one"
        )

    def test_a_number_outside_the_scale_does_not_become_the_highest(self, app):
        """It used to. That made sense while there was a ceiling to
        squeeze toward; with none, it reads a corrupted row as a request
        for the most autonomy there is."""
        from api.services.chat_session.settings import TrustLevel

        level = TrustLevel()
        assert level.clamp(99) == level.STANDARD
        assert level.clamp(99) != level.HIGHEST

    def test_clamp_and_check_agree_about_what_a_level_is(self, app):
        """One rule, asked two ways. They differ in what they DO about a
        value that is not a level — refuse it, or stand it down to the
        standard one — and never in which values those are."""
        from api.services.chat_session.settings import TrustLevel

        level = TrustLevel()
        for value in (0, 1, 2, 3, 4, -1, 99, "high", None, [3], 2.5):
            accepted, problem = level.check({}, value)
            clamped = level.clamp(value)
            if problem:
                assert clamped == level.STANDARD, value
            else:
                assert clamped == accepted, value


class TestAChatChoosesItsOwn:
    def test_every_level_is_available(self, admin, seed):
        """Nothing above the person refuses a level any more."""
        chat_id = app_call(admin, "AI:Chat:Create", {"request_id": "t1"}) \
            .json()["data"]["chat"]["chat_id"]

        for level in (0, 1, 2, 3):
            updated = app_call(admin, "AI:Chat:Update", {
                "chat_id": chat_id, "config": {"trust_level": level},
            })
            assert updated.status_code == 200, updated.text
            served = app_call(admin, "AI:Chat:Get", {"chat_id": chat_id}) \
                .json()["data"]["chat"]["config"]
            assert served["trust_level"] == level

    def test_a_level_outside_the_scale_is_still_refused(self, admin, seed):
        chat_id = app_call(admin, "AI:Chat:Create", {"request_id": "t2"}) \
            .json()["data"]["chat"]["chat_id"]
        response = app_call(admin, "AI:Chat:Update", {
            "chat_id": chat_id, "config": {"trust_level": 7},
        })
        assert response.status_code >= 400


class TestBudgets:
    """Turns are the user's patience; steps are the agent manifest's."""

    def test_a_chat_may_set_its_own_turns(self, admin, seed):
        chat_id = app_call(admin, "AI:Chat:Create", {"request_id": "b1"}) \
            .json()["data"]["chat"]["chat_id"]
        assert app_call(admin, "AI:Chat:Update", {
            "chat_id": chat_id, "config": {"max_turns": 200},
        }).status_code == 200

        served = app_call(admin, "AI:Chat:Get", {"chat_id": chat_id}) \
            .json()["data"]
        assert served["budgets"]["max_turns"] == 200

    def test_a_chat_that_asks_for_nothing_gets_the_standard(self, admin, seed):
        from api.services.chat_session.settings import TurnBudget

        chat_id = app_call(admin, "AI:Chat:Create", {"request_id": "b2"}) \
            .json()["data"]["chat"]["chat_id"]
        served = app_call(admin, "AI:Chat:Get", {"chat_id": chat_id}) \
            .json()["data"]
        assert served["budgets"]["max_turns"] == TurnBudget().STANDARD

    def test_a_shape_that_is_not_a_budget_is_refused(self, admin, seed):
        from api.services.chat_session.settings import TurnBudget

        turns = TurnBudget()
        assert turns.check({}, -1)[0] is None
        assert turns.check({}, "many")[0] is None
        # Not a policy — a typo or a probe, refused for its shape.
        assert turns.check({}, turns.LARGEST + 1)[0] is None
        assert turns.check({}, 50) == (50, "")

    def test_a_stored_budget_stands_down_rather_than_squeezing(self, app):
        """A corrupted row of 999999 is not a request for the longest run
        this platform will do."""
        from api.services.chat_session.settings import TurnBudget

        turns = TurnBudget()
        assert turns.clamp(50) == 50
        for damaged in (-1, turns.LARGEST + 1, "many", None, []):
            assert turns.clamp(damaged) == turns.STANDARD, damaged

    def test_zero_is_unlimited_and_travels(self, admin, seed):
        """No valve: the page offers it, the contract carries it as 0,
        and the runtime reads 0 as never pausing."""
        from api.services.chat_session.settings import TurnBudget

        turns = TurnBudget()
        assert turns.check({}, 0) == (0, "")
        assert turns.clamp(0) == 0
        chat_id = app_call(admin, "AI:Chat:Create", {"request_id": "b3"}) \
            .json()["data"]["chat"]["chat_id"]
        assert app_call(admin, "AI:Chat:Update", {
            "chat_id": chat_id, "config": {"max_turns": 0},
        }).status_code == 200
        served = app_call(admin, "AI:Chat:Get", {"chat_id": chat_id}) \
            .json()["data"]
        assert served["budgets"]["max_turns"] == 0

    def test_clamp_and_check_agree_about_what_a_budget_is(self, app):
        """The rule TrustLevel keeps, kept here too."""
        from api.services.chat_session.settings import TurnBudget

        turns = TurnBudget()
        for value in (1, 20, 50, turns.LARGEST, turns.LARGEST + 1, 0, -1,
                      "many", None, [20], 7.4):
            accepted, problem = turns.check({}, value)
            clamped = turns.clamp(value)
            if problem:
                assert clamped == turns.STANDARD, value
            else:
                assert clamped == accepted, value

    def test_nothing_is_said_about_agent_steps(self, admin, seed):
        """The manifest declares what an agent needs. Sending a number
        would override the one somebody reviewed."""
        chat_id = app_call(admin, "AI:Chat:Create", {"request_id": "b3"}) \
            .json()["data"]["chat"]["chat_id"]
        served = app_call(admin, "AI:Chat:Get", {"chat_id": chat_id}) \
            .json()["data"]
        assert "max_agent_steps" not in served["budgets"]

    def test_nothing_computed_rides_inside_the_config(self, admin, seed):
        """A chat config holds what a PERSON chose — their own value, or
        the default they set. What the backend worked out travels beside
        the chat, because the page writes the whole config back when one
        setting changes and would persist it as a choice."""
        chat_id = app_call(admin, "AI:Chat:Create", {"request_id": "b4"}) \
            .json()["data"]["chat"]["chat_id"]
        served = app_call(admin, "AI:Chat:Get", {"chat_id": chat_id}) \
            .json()["data"]
        assert "max_agent_steps" not in (served["chat"]["config"] or {})
        assert served["budgets"]["max_turns"] > 0


class TestDefaultsBecomeAChats:
    """A choice made once should not be made again per conversation."""

    def test_a_default_level_is_what_a_new_chat_starts_at(self, admin, seed):
        assert app_call(admin, "Account:Profile:Update", {
            "preferences": {"chat": {"trust_level": 3}},
        }).status_code == 200

        config = app_call(admin, "AI:Chat:Create", {"request_id": "d1"}) \
            .json()["data"]["chat"]["config"]
        assert config["trust_level"] == 3

    def test_a_default_patience_is_too(self, admin, seed):
        assert app_call(admin, "Account:Profile:Update", {
            "preferences": {"chat": {"max_turns": 75}},
        }).status_code == 200

        served = app_call(admin, "AI:Chat:Create", {"request_id": "d2"}) \
            .json()["data"]
        assert served["chat"]["config"]["max_turns"] == 75

    def test_a_chat_still_overrides_the_default(self, admin, seed):
        app_call(admin, "Account:Profile:Update", {
            "preferences": {"chat": {"trust_level": 3}},
        })
        config = app_call(admin, "AI:Chat:Create", {
            "request_id": "d3", "config": {"trust_level": 0},
        }).json()["data"]["chat"]["config"]
        assert config["trust_level"] == 0

    def test_changing_a_default_leaves_existing_chats_alone(self, admin, seed):
        """A conversation owns its settings once it exists. A chat that
        quietly became autonomous because somebody edited a preference is
        the surprise this avoids."""
        app_call(admin, "Account:Profile:Update", {
            "preferences": {"chat": {"trust_level": 0}},
        })
        chat_id = app_call(admin, "AI:Chat:Create", {"request_id": "d4"}) \
            .json()["data"]["chat"]["chat_id"]

        app_call(admin, "Account:Profile:Update", {
            "preferences": {"chat": {"trust_level": 3}},
        })
        config = app_call(admin, "AI:Chat:Get", {"chat_id": chat_id}) \
            .json()["data"]["chat"]["config"]
        assert config["trust_level"] == 0

    def test_a_preference_that_is_not_a_setting_is_refused(self, admin, seed):
        """The accepted keys are the settings themselves, so this door
        cannot fall behind the vocabulary as a hand-written list did."""
        response = app_call(admin, "Account:Profile:Update", {
            "preferences": {"chat": {"trust_ceiling": 2}},
        })
        assert response.status_code == 400
        assert "trust_ceiling" in response.json()["error"]


class TestAModelIsCheckedNotTrusted:
    def test_a_chat_cannot_name_a_key_that_is_not_there(self, admin, seed):
        response = app_call(admin, "AI:Chat:Create", {
            "request_id": "m1",
            "config": {"llm": {"provider": "anthropic", "model": "x",
                               "secret_ref": "secret_nobody_has"}},
        })
        assert response.status_code >= 400
        assert "not available to you" in response.text

    def test_a_secret_that_is_not_a_connection_is_refused(self, admin, seed):
        """An llm block names one of the organization's LLM connections.
        A secret this person can see — however real — is not one."""
        define_secret("repo_probe", "Probe",
                      [{"name": "token", "type": "secret", "required": True}])
        other = app_call(admin, "Secrets:Secret:Create", {
            "definition_id": "repo_probe", "name": "repo",
            "fields": {"token": "t"},
        })
        assert other.status_code == 200, other.text

        response = app_call(admin, "AI:Chat:Create", {
            "request_id": "m3",
            "config": {"llm": {"provider": "anthropic", "model": "x",
                               "secret_ref": other.json()["resource"]
                               ["resource_ref"]}},
        })
        assert response.status_code >= 400
        assert "not available to you" in response.text

    def test_a_block_with_no_connection_is_carried_through_as_sent(self, admin, seed):
        """The block is the RUNTIME's input. The scripted connector that
        smoke checks run on needs no key and rides with keys of its own;
        nothing about it is rewritten."""
        config = app_call(admin, "AI:Chat:Create", {
            "request_id": "m2",
            "config": {"llm": {"provider": "fake", "responses": ["hi"]}},
        }).json()["data"]["chat"]["config"]

        assert config["llm"] == {"provider": "fake", "responses": ["hi"]}

    def test_a_block_that_names_a_connection_takes_its_provider_from_it(self, admin, seed):
        """Whose key it is and where it is sent are the connection's —
        the runtime takes them from the connection whatever a block
        says, so the stored block says the same. The model stays the
        chat's own."""
        ref = make_llm_connection(admin)
        config = app_call(admin, "AI:Chat:Create", {
            "request_id": "m4",
            "config": {"llm": {"provider": "fake", "model": "another-model",
                               "secret_ref": ref}},
        }).json()["data"]["chat"]["config"]

        assert config["llm"]["secret_ref"] == ref
        assert config["llm"]["provider"] != "fake"
        assert config["llm"]["model"] == "another-model"


class TestAgentsAreBoundedOnBothDoors:
    def test_creating_cannot_reach_past_the_default_either(self, admin, seed):
        """Updating a chat was checked and creating one was not, so a chat
        could be born holding agents its owner had turned off, and simply
        not be allowed to change afterwards."""
        assert app_call(admin, "Account:Profile:Update", {
            "preferences": {"chat": {"enabled_agents": []}},
        }).status_code == 200

        response = app_call(admin, "AI:Chat:Create", {
            "request_id": "a1", "config": {"enabled_agents": ["anything"]},
        })
        assert response.status_code >= 400
        assert "enabled in your defaults" in response.text


class TestWhatAConfigHolds:
    def test_a_key_no_setting_owns_is_not_kept(self, admin, seed):
        """Nothing reads it — the runtime reads the contract — so it
        is not stored."""
        chat_id = app_call(admin, "AI:Chat:Create", {"request_id": "k1"}) \
            .json()["data"]["chat"]["chat_id"]
        assert app_call(admin, "AI:Chat:Update", {
            "chat_id": chat_id,
            "config": {"trust_level": 1, "anything": "x" * 1000},
        }).status_code == 200
        served = app_call(admin, "AI:Chat:Get", {"chat_id": chat_id}) \
            .json()["data"]["chat"]["config"]
        assert served == {"trust_level": 1}

    def test_bindings_map_a_slot_to_a_credential(self, admin, seed):
        chat_id = app_call(admin, "AI:Chat:Create", {"request_id": "k2"}) \
            .json()["data"]["chat"]["chat_id"]
        for wrong in ("a ref", {"agt_x__mail": 7}, {"../x": "ref"}):
            refused = app_call(admin, "AI:Chat:Update", {
                "chat_id": chat_id, "config": {"bindings": wrong}})
            assert refused.status_code == 400, wrong
        assert app_call(admin, "AI:Chat:Update", {
            "chat_id": chat_id,
            "config": {"bindings": {"agt_x__mail": "abc123"}},
        }).status_code == 200


class TestTheZoneAChatKeepsTimeIn:
    """The sixth setting: the person's IANA zone, sent by the page. A
    chat keeps it; the runtime stamps its clock and counts cron
    cadences in it (contracts/cron.py)."""

    def test_a_real_zone_passes_and_nonsense_is_refused(self, app):
        from api.services.chat_session.settings import Timezone

        assert Timezone().check({}, "Asia/Dubai") == ("Asia/Dubai", "")
        assert Timezone().check({}, " Europe/London ") == ("Europe/London", "")
        for bad in ("Mars/Olympus", "Dubai", "", None, 4):
            value, problem = Timezone().check({}, bad)
            assert value is None, bad
            assert "IANA" in problem

    def test_a_stored_zone_that_is_not_one_is_served_as_none(self, app):
        from api.services.chat_session.settings import Timezone

        assert Timezone().clamp("Asia/Dubai") == "Asia/Dubai"
        assert Timezone().clamp("nonsense") is None

    def test_a_chat_keeps_the_zone_it_was_created_with(self, admin, seed):
        response = app_call(admin, "AI:Chat:Create", {
            "config": {"timezone": "Asia/Dubai"}})
        assert response.status_code == 200, response.text
        chat = response.json()["data"]["chat"]
        assert chat["config"]["timezone"] == "Asia/Dubai"
        opened = app_call(admin, "AI:Chat:Open", {
            "chat_id": chat["chat_id"], "timezone": "Europe/London"})
        # Opening from elsewhere does not move its reminders.
        assert opened.json()["data"]["contract"]["timezone"] == "Asia/Dubai"

    def test_a_chat_from_before_zones_takes_the_pages_once(self, admin, seed):
        chat_id = app_call(admin, "AI:Chat:Create", {}).json()["data"]["chat"]["chat_id"]
        from database.stores import ChatStore

        stored = ChatStore().by_chat_id(chat_id)
        assert "timezone" not in (stored.get("config") or {})

        opened = app_call(admin, "AI:Chat:Open", {
            "chat_id": chat_id, "timezone": "Asia/Dubai"})
        assert opened.json()["data"]["contract"]["timezone"] == "Asia/Dubai"
        assert ChatStore().by_chat_id(chat_id)["config"]["timezone"] == "Asia/Dubai"
        # Nonsense from a page is ignored, not stored.
        app_call(admin, "AI:Chat:Open", {"chat_id": chat_id, "timezone": "??"})
        assert ChatStore().by_chat_id(chat_id)["config"]["timezone"] == "Asia/Dubai"

    def test_a_person_may_hold_a_default_zone(self, admin, seed):
        saved = app_call(admin, "Account:Profile:Update", {
            "preferences": {"chat": {"timezone": "Europe/London"}}})
        assert saved.status_code == 200, saved.text
        chat = app_call(admin, "AI:Chat:Create", {}).json()["data"]["chat"]
        assert chat["config"]["timezone"] == "Europe/London"

    def test_the_runtime_reads_the_zone_from_the_contract(
            self, anon, admin, seed, signing_key):
        from test_runtime_secret_use import runtime_call

        chat_id = app_call(admin, "AI:Chat:Create", {
            "config": {"timezone": "Asia/Dubai"}}).json()["data"]["chat"]["chat_id"]
        contract = runtime_call(
            anon, seed, chat_id, "AI:Chat:Contract", {}).json()["data"]
        assert contract["timezone"] == "Asia/Dubai"


class TestSkillsCap:
    """How many skills a turn is shown: the platform's forty unless the
    chat says otherwise, and zero lists them all."""

    def test_a_chat_may_set_how_many_skills_it_is_shown(self, admin, seed):
        chat_id = app_call(admin, "AI:Chat:Create", {"request_id": "s1"}) \
            .json()["data"]["chat"]["chat_id"]
        assert app_call(admin, "AI:Chat:Update", {
            "chat_id": chat_id, "config": {"max_skills": 120},
        }).status_code == 200
        served = app_call(admin, "AI:Chat:Get", {"chat_id": chat_id}) \
            .json()["data"]
        assert served["budgets"]["max_skills"] == 120

    def test_the_standard_is_forty_and_zero_is_all(self, admin, seed):
        from api.services.chat_session.settings import SkillsCap

        cap = SkillsCap()
        assert cap.STANDARD == 40
        chat_id = app_call(admin, "AI:Chat:Create", {"request_id": "s2"}) \
            .json()["data"]["chat"]["chat_id"]
        served = app_call(admin, "AI:Chat:Get", {"chat_id": chat_id}) \
            .json()["data"]
        assert served["budgets"]["max_skills"] == 40
        assert app_call(admin, "AI:Chat:Update", {
            "chat_id": chat_id, "config": {"max_skills": 0},
        }).status_code == 200
        served = app_call(admin, "AI:Chat:Get", {"chat_id": chat_id}) \
            .json()["data"]
        assert served["budgets"]["max_skills"] == 0

    def test_a_shape_that_is_not_a_cap_is_refused_and_a_stored_one_stands_down(self, app):
        from api.services.chat_session.settings import SkillsCap

        cap = SkillsCap()
        assert cap.check({}, -1)[0] is None
        assert cap.check({}, "many")[0] is None
        assert cap.check({}, cap.LARGEST + 1)[0] is None
        assert cap.check({}, 80) == (80, "")
        assert cap.clamp(80) == 80
        assert cap.clamp(0) == 0
        for damaged in (-1, cap.LARGEST + 1, "many", None, []):
            assert cap.clamp(damaged) == cap.STANDARD, damaged
