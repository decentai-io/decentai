"""Records: one shape for secrets, data and files; an agent reaches only its own; handing on, sharing and the gate
(api-server.md §3, §6; agents.md §6, §7)."""

from __future__ import annotations

import pytest

from conftest import NOTES, JOURNAL, World
from api_server.chats.store import Cards, Events
from api_server.people.store import Groups, Policies
from api_server.records.store import Records


@pytest.fixture
def chat(world, org, runtime):
    """The member's chat, held by the runtime, using both agents."""
    World.install(org, NOTES, JOURNAL)
    chat = world.ok("AI:Chat:create", {}, org.member)["chat"]
    runtime.holds(chat["chat_id"])
    return chat


def agent(world, runtime, chat, operation, call="c1", name="notes", function="keep", **data):
    return world.call(f"Records:Record:{operation}", {"chat_id": chat["chat_id"], "agent": name, "function": function,
                                                      "call": call, **data}, runtime.caller)


def note(world, caller, title="A note", **fields):
    return world.ok("Data:Record:create", {"of": {"agent": "notes", "resource": "notes"},
                                           "fields": {"title": title, **fields}}, caller)["record"]


class TestShape:
    def test_a_secrets_values_never_go_back_to_a_person(self, world, org, chat):
        world.ok("Secrets:Secret:create", {"of": {"agent": "notes", "resource": "mailbox"},
                                           "fields": {"server": "mail.x", "password": "hunter22"}}, org.member)
        listed = world.ok("Secrets:Secret:list", {}, org.member)["records"][0]
        assert listed["keys"] == {"server": "mail.x"} and listed["values"] == {"password": None}
        assert "hunter22" not in str(listed)

    def test_values_are_bound_to_their_record(self, world, org, chat):
        first, second = note(world, org.member, body="first"), note(world, org.member, body="second")
        moved = Records().col.find_one({"_id": first["record_id"]})["values"]
        Records().col.update_one({"_id": second["record_id"]}, {"$set": {"values": moved}})
        with pytest.raises(ValueError):
            Records().opened(Records().col.find_one({"_id": second["record_id"]}))

    def test_a_field_larger_than_it_may_be_is_refused(self, world, org, chat):
        world.refused("Data:Record:create", {"of": {"agent": "notes", "resource": "notes"},
                                             "fields": {"title": "Big", "body": "x" * 600_000}}, org.member, 400)


class TestAnAgentsReach:
    def test_an_agent_reaches_only_records_that_list_it(self, world, org, runtime, chat):
        mine = note(world, org.member)
        journal = Records().create(org.member.person, "data", {"title": "J"}, {}, {"agent": "journal",
                                                                                  "resource": "entries"})
        listed, _ = agent(world, runtime, chat, "list", resource="notes")
        assert [r["ref"] for r in listed["records"]] == [mine["record_id"]]
        assert agent(world, runtime, chat, "read", resource="notes", ref=journal["_id"])[1] == 404

    def test_a_record_another_person_keeps_is_out_of_reach(self, world, org, runtime, chat):
        theirs = note(world, org.admin)
        assert agent(world, runtime, chat, "read", resource="notes", ref=theirs["record_id"])[1] == 404

    def test_a_call_must_name_a_chats_agent_and_a_call_its_function_declares(self, world, runtime, chat):
        assert agent(world, runtime, chat, "list", name="", resource="notes")[1] == 403
        assert "declare" in agent(world, runtime, chat, "list", resource="pictures")[0]["error"]["message"]

    def test_a_runtime_that_doesnt_hold_the_chat_is_refused(self, world, org, chat, monkeypatch):
        from conftest import Runtime
        other = Runtime(monkeypatch)
        answer, status = world.call("Records:Record:list", {"chat_id": chat["chat_id"], "agent": "notes",
                                                           "function": "keep", "resource": "notes"}, other.caller)
        assert status == 409 and answer["error"]["reason"] == "stale"

    def test_a_secrets_values_wait_for_their_person_below_trust_two(self, world, org, runtime, chat):
        made = world.ok("Secrets:Secret:create", {"of": {"agent": "notes", "resource": "mailbox"},
                                                  "fields": {"server": "mail.x", "password": "hunter22"}}, org.member)
        ref = made["record"]["record_id"]
        answer, status = agent(world, runtime, chat, "read", call="c9", resource="mailbox", ref=ref)
        assert status == 403 and answer["error"]["approval"] is True
        item = {"id": "c9.1", "agent": "notes", "function": "keep", "call": "record.read:mailbox"}
        card = world.ok("AI:Approval:Open", {"chat_id": chat["chat_id"], "kind": "approval", "agent": "notes",
                                             "request": {"items": [item]}}, runtime.caller)
        world.ok("AI:Approval:Decide", {"card_id": card["card_id"], "answer": {"items": {"c9.1": True}}}, org.member)
        answer, status = agent(world, runtime, chat, "read", call="c9", resource="mailbox", ref=ref)
        assert status == 200 and answer["fields"]["password"] == "hunter22"

    def test_a_policys_deny_wins_over_everything(self, world, org, runtime, chat):
        mine = note(world, org.member)
        policy = {"_id": Policies.new_id(), "org_id": org.org_id, "name": "No deleting",
                  "statements": [{"effect": "deny", "actions": ["agent:notes:keep:record.delete:*"]}]}
        Policies().col.insert_one(policy)
        Groups().col.update_one({"org_id": org.org_id, "everyone": True}, {"$push": {"policies": policy["_id"]}})
        assert agent(world, runtime, chat, "delete", resource="notes", ref=mine["record_id"])[1] == 403

    def test_a_secret_left_empty_puts_its_declared_form_before_the_person(self, world, org, runtime, chat):
        made, _ = agent(world, runtime, chat, "create", resource="mailbox", fields={"server": "mail.y"})
        card = Cards().col.find_one({"_id": made["waiting"]})
        assert card["kind"] == "form" and [f["name"] for f in card["request"]["fields"]] == ["password"]
        world.ok("AI:Approval:Decide", {"card_id": card["_id"], "answer": {"values": {"password": "pw-1"}}}, org.member)
        record = Records().col.find_one({"_id": made["ref"]})
        assert Records().opened(record)["password"] == "pw-1"
        assert runtime.frames("answer")[-1]["card"] == card["_id"]


class TestSharing:
    def test_handing_on_waits_for_the_recipient_to_accept(self, world, org, chat):
        mine = note(world, org.member)
        world.ok("Data:Record:transfer", {"record_id": mine["record_id"], "user_id": org.admin.user_id}, org.member)
        assert Records().col.find_one({"_id": mine["record_id"]})["created_by"] == org.member.user_id
        world.ok("Data:Record:accept", {"record_id": mine["record_id"], "accept": True}, org.admin)
        record = Records().col.find_one({"_id": mine["record_id"]})
        assert record["created_by"] == org.admin.user_id and org.member.user_id not in record["owner"]["users"]

    def test_an_agent_is_added_by_hand_only_when_the_record_fits(self, world, org, chat):
        mine = note(world, org.member)
        fits = world.ok("Data:Record:update", {"record_id": mine["record_id"], "agents": [
            {"agent": "journal", "resource": "entries"}]}, org.member)["record"]
        assert {"agent": "journal", "resource": "entries"} in fits["agents"]
        world.refused("Data:Record:update", {"record_id": mine["record_id"], "agents": [
            {"agent": "journal", "resource": "scores"}]}, org.member, 400)

    def test_manage_any_deletes_another_persons_record_but_never_shares_it(self, world, org, chat):
        mine = note(world, org.member)
        world.refused("Data:Record:update", {"record_id": mine["record_id"], "owner": {"users": [org.admin.user_id]}},
                      org.admin, 404)
        world.ok("Data:Record:delete", {"record_id": mine["record_id"]}, org.admin)
        assert Records().col.find_one({"_id": mine["record_id"]}) is None

    def test_sharing_beyond_ones_own_groups_is_refused(self, world, org, chat):
        mine = note(world, org.member)
        admins = Groups().col.find_one({"org_id": org.org_id, "name": "Administrators"})
        world.refused("Data:Record:update", {"record_id": mine["record_id"], "owner": {"groups": [admins["_id"]]}},
                      org.member, 403)


class TestShown:
    def test_a_form_card_is_shown_as_it_opens(self, world, org, runtime, chat):
        made, _ = agent(world, runtime, chat, "create", resource="mailbox", fields={"server": "mail.y"})
        assert Events().col.find_one({"chat_id": chat["chat_id"], "event": "card_opened", "data.card_id": made["waiting"]})

    def test_one_record_is_read_by_its_id(self, world, org, chat):
        mine = note(world, org.member)
        assert world.ok("Data:Record:get", {"record_id": mine["record_id"]}, org.member)["record"]["keys"] == {"title": "A note"}
        world.refused("Data:Record:get", {"record_id": mine["record_id"]}, org.admin, 404)


class TestNamedAndWithdrawn:
    def test_a_record_has_the_name_its_person_gives_it(self, world, org, chat):
        made = world.ok("Data:Record:create", {"of": {"agent": "notes", "resource": "notes"}, "name": "  Plans  ",
                                               "fields": {"title": "A"}}, org.member)["record"]
        assert made["name"] == "Plans"
        renamed = world.ok("Data:Record:update", {"record_id": made["record_id"], "name": "Plans for May"}, org.member)
        assert renamed["record"]["name"] == "Plans for May"

    def test_a_hand_over_is_withdrawn_before_it_is_accepted(self, world, org, chat):
        mine = note(world, org.member)
        world.ok("Data:Record:transfer", {"record_id": mine["record_id"], "user_id": org.admin.user_id}, org.member)
        world.refused("Data:Record:withdraw", {"record_id": mine["record_id"]}, org.admin, 404)
        assert world.ok("Data:Record:withdraw", {"record_id": mine["record_id"]}, org.member)["record"]["handing"] is None
        world.refused("Data:Record:accept", {"record_id": mine["record_id"], "accept": True}, org.admin, 404)


class TestFormsAndPages:
    def test_a_forms_questions_left_unanswered_are_asked_of_the_person(self, world, org, runtime, chat):
        asked = {"user": {"type": "text", "remember": True}, "code": {"type": "secret", "remember": False}}
        made, _ = agent(world, runtime, chat, "create", resource="logins", fields={"site": asked})
        card = Cards().col.find_one({"_id": made["waiting"]})
        assert [f["name"] for f in card["request"]["fields"]] == ["site"] and card["request"]["questions"] == {"site": asked}
        answer = {"site": {"user": {"type": "text", "remember": True, "value": "ana"},
                           "code": {"type": "secret", "remember": False, "value": "123456"}}}
        world.ok("AI:Approval:Decide", {"card_id": card["_id"], "answer": {"values": answer}}, org.member)
        assert Records().opened(Records().col.find_one({"_id": made["ref"]}))["site"] == {"user": answer["site"]["user"]}
        assert runtime.frames("answer")[-1]["answer"]["once"] == {"code": "123456"}

    def test_an_agent_lists_its_records_a_page_at_a_time(self, world, org, runtime, chat):
        made = sorted(note(world, org.member, f"N{n}")["record_id"] for n in range(5))
        first, _ = agent(world, runtime, chat, "list", resource="notes", limit=3)
        second, _ = agent(world, runtime, chat, "list", resource="notes", limit=3, after=first["next"])
        assert [r["ref"] for r in first["records"] + second["records"]] == made and second["next"] is None
