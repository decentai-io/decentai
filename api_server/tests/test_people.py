"""Calls and answers, who calls, people and permissions (api-server.md §2, §3, §4)."""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

import pytest

from conftest import JOURNAL, NOTES, PASSWORD, World
from api_server.doors.callers import Callers
from api_server.doors.calls import Refused
from api_server.doors.limits import Limits
from api_server.people.permissions import Rules
from api_server.people.store import ApiKeys, Deployment, Groups, Links, Policies, Sessions, Users
from api_server.records.store import Records
from api_server.trail.trail import Trail
from api_server.utils import Settings


class TestCalls:
    def test_an_unknown_endpoint_is_not_found_in_the_one_shape(self, world, org):
        error = world.refused("Nothing:Here:at_all", {}, org.member, 404)
        assert set(error) == {"code", "message"} and error["code"] == "not_found"

    def test_a_person_endpoint_without_a_session_is_signed_out(self, world):
        assert world.refused("AI:Chat:list", {}, world.anyone(), 401)["code"] == "signed_out"

    def test_a_refusal_is_written_on_the_trail(self, world, org):
        assert world.refused("IAM:User:list", {}, org.member, 403)["code"] == "refused"
        line = Trail().col.find_one({"who": org.member_email, "what": "iam:user:list"})
        assert line["outcome"] == "refused" and line["org_id"] == org.org_id

    def test_a_change_is_written_on_the_trail_and_a_read_is_not(self, world, org):
        world.ok("AI:Chat:list", {}, org.member)
        world.ok("AI:Chat:create", {"title": "Hello"}, org.member)
        whats = {line["what"] for line in Trail().col.find({"who": org.member_email})}
        assert "ai:chat:create" in whats and "ai:chat:list" not in whats

    def test_a_runtime_cannot_call_a_persons_endpoint(self, world, runtime):
        assert world.refused("AI:Chat:list", {}, runtime.caller, 403)["code"] == "refused"

    def test_too_many_calls_are_refused_with_when_to_try_again(self, world, org, monkeypatch):
        monkeypatch.setattr(Settings.read(), "calls_per_minute", 3)
        for _ in range(3):
            world.ok("AI:Chat:list", {}, org.member)
        error = world.refused("AI:Chat:list", {}, org.member, 429)
        assert error["code"] == "too_many" and error["retry_after"] > 0


    def test_calls_are_counted_in_memory_and_learn_what_every_server_added(self, world):
        key, limits = f"calls:{uuid.uuid4().hex}", Limits()
        for _ in range(2):
            limits.counted(key, 5, 60, "calls")
        limits.flush()
        assert limits.col.find_one({"_id": key})["count"] == 2
        limits.col.update_one({"_id": key}, {"$inc": {"count": 3}})  # another server's
        limits.counted(key, 5, 60, "calls")
        limits.flush()
        with pytest.raises(Refused, match="Too many calls"):
            limits.counted(key, 5, 60, "calls")


class TestSigningIn:
    def test_a_session_token_is_random_and_kept_only_as_its_hash(self, world, org):
        token = world.ok("Auth:Session:create", {"email": org.member_email, "password": PASSWORD})["token"]
        assert Sessions().col.find_one({"_id": hashlib.sha256(token.encode()).hexdigest()}) is not None
        assert Sessions().col.find_one({"_id": token}) is None
        assert Callers().person(token, "10.0.0.1").person["email"] == org.member_email

    def test_wrong_passwords_from_one_address_never_lock_the_person_out_from_another(self, world, org):
        for _ in range(5):
            world.refused("Auth:Session:create", {"email": org.member_email, "password": "wrong-password-x"},
                          world.anyone("10.0.0.66"), 403)
        world.refused("Auth:Session:create", {"email": org.member_email, "password": PASSWORD},
                      world.anyone("10.0.0.66"), 429)
        world.ok("Auth:Session:create", {"email": org.member_email, "password": PASSWORD}, world.anyone("10.0.0.77"))

    def test_a_disabled_person_is_refused_on_their_next_call(self, world, org):
        token = world.ok("Auth:Session:create", {"email": org.member_email, "password": PASSWORD})["token"]
        member = Users().by_email(org.member_email)
        world.ok("IAM:User:set_status", {"user_id": member["_id"], "status": "disabled"}, org.admin)
        assert Callers().person(token, "10.0.0.1").kind == "anyone"

    def test_an_api_key_has_an_expiry_and_ends_with_it(self, world, org):
        world.refused("Settings:ApiKey:create", {"name": "k", "days": 400}, org.member, 400)
        key = world.ok("Settings:ApiKey:create", {"name": "k", "days": 30}, org.member)["key"]
        assert key not in str(world.ok("Settings:ApiKey:list", {}, org.member))
        assert Callers().person(key, "10.0.0.1").kind == "person"
        ApiKeys().col.update_one({"user_id": org.member.user_id}, {"$set": {"expires_at": ApiKeys.now() - timedelta(1)}})
        assert Callers().person(key, "10.0.0.1").kind == "anyone"


class TestJoining:
    def test_first_run_takes_the_setup_code_once(self, world):
        Deployment().set(first_run_done=False, setup_code="the-setup-code")
        made = {"organization": "First", "name": "Fay", "email": "fay@x.test", "password": PASSWORD}
        world.refused("Auth:Join:setup", {**made, "code": "not-it"}, world.anyone("10.1.0.1"), 403)
        assert world.ok("Auth:Join:setup", {**made, "code": "the-setup-code"}, world.anyone("10.1.0.1"))["token"]
        world.refused("Auth:Join:setup", {**made, "email": "fay2@x.test", "code": "the-setup-code"},
                      world.anyone("10.1.0.1"), 403)

    def test_an_invitation_is_mailed_and_accepted_once(self, world, org, mail):
        world.ok("IAM:Invitation:create", {"email": "new@x.test"}, org.admin)
        token = mail[-1]["link"].split("token=")[1]
        accept = {"token": token, "name": "New", "password": PASSWORD}
        assert world.ok("Auth:Join:accept", accept)["person"]["email"] == "new@x.test"
        world.refused("Auth:Join:accept", accept, None, 404)

    def test_an_invitation_to_the_same_address_waits_a_minute(self, world, org, mail):
        world.ok("IAM:Invitation:create", {"email": "twice@x.test"}, org.admin)
        world.refused("IAM:Invitation:create", {"email": "twice@x.test"}, org.admin, 429)

    def test_without_mail_a_person_is_added_with_a_temporary_password_they_replace(self, world, org):
        password = world.ok("IAM:User:create", {"email": "temp@x.test", "name": "Tem"}, org.admin)["password"]
        error = world.refused("Auth:Session:create", {"email": "temp@x.test", "password": password}, None, 409)
        assert error["change_required"] is True
        world.ok("Auth:Session:first", {"email": "temp@x.test", "current_password": password, "new_password": PASSWORD})

    def test_a_forgotten_password_is_reset_through_its_link_once(self, world, org, mail):
        token = world.ok("Auth:Session:create", {"email": org.member_email, "password": PASSWORD})["token"]
        world.ok("Auth:Password:forgot", {"email": org.member_email}, world.anyone("10.2.0.1"))
        link = mail[-1]["link"].split("token=")[1]
        world.ok("Auth:Password:reset", {"token": link, "password": "another-password-2"})
        world.refused("Auth:Password:reset", {"token": link, "password": "another-password-3"}, None, 404)
        assert Callers().person(token, "10.0.0.1").kind == "anyone"

    def test_forgot_answers_the_same_for_an_address_with_no_account(self, world, mail):
        world.ok("Auth:Password:forgot", {"email": "nobody@x.test"}, world.anyone("10.3.0.1"))
        assert mail == []


class TestPermissions:
    def _delegate(self, world, org, actions):
        """The member placed in a group whose policy allows these actions."""
        policy = world.ok("IAM:Policy:create", {"name": f"P{Policies.new_id()}", "statements": [
            {"effect": "allow", "actions": actions}]}, org.admin)["policy"]
        group = world.ok("IAM:Group:create", {"name": f"G{Groups.new_id()}"}, org.admin)["group"]
        world.ok("IAM:Group:set_policies", {"group_id": group["group_id"], "policies": [policy["policy_id"]]}, org.admin)
        world.ok("IAM:User:set_groups", {"user_id": org.member.user_id, "groups": [group["group_id"]]}, org.admin)

    def test_a_delegated_administrator_cannot_demote_the_founder(self, world, org):
        self._delegate(world, org, ["iam:user:*", "iam:group:*"])
        error = world.refused("IAM:User:set_groups", {"user_id": org.admin.user_id, "groups": []}, org.member, 403)
        assert "hold" in error["message"]

    def test_nobody_grants_what_they_do_not_hold(self, world, org):
        self._delegate(world, org, ["iam:policy:*"])
        world.refused("IAM:Policy:create", {"name": "All", "statements": [{"effect": "allow", "actions": ["*"]}]},
                      org.member, 403)

    def test_an_organization_always_keeps_someone_who_manages_access(self, world, org):
        error = world.refused("IAM:User:set_groups", {"user_id": org.admin.user_id, "groups": []}, org.admin, 403)
        assert "manage access" in error["message"]
        assert Users().by_email(org.admin_email)["groups"]
        # Once the member manages access through one of the organization's own policies, the founder may step down.
        self._delegate(world, org, ["iam:user:*", "iam:group:*"])
        world.ok("IAM:User:set_groups", {"user_id": org.admin.user_id, "groups": []}, org.admin)

    def test_deny_wins_then_ask_then_allow_and_ask_is_for_agents_alone(self, world, org):
        statements = [{"effect": "allow", "actions": ["agent:*"]}, {"effect": "ask", "actions": ["agent:notes:*"]},
                      {"effect": "deny", "actions": ["agent:notes:keep:record.delete:*"]}]
        assert Rules.evaluate(statements, "agent:journal:write") == "allow"
        assert Rules.evaluate(statements, "agent:notes:keep") == "ask"
        assert Rules.evaluate(statements, "agent:notes:keep:record.delete:notes") == "deny"
        assert Rules.evaluate(statements, "iam:user:list") == "deny"
        world.refused("IAM:Policy:create", {"name": "Ask", "statements": [{"effect": "ask", "actions": ["iam:user:list"]}]},
                      org.admin, 400)

    def test_the_built_in_policies_cannot_be_changed(self, world, org):
        world.refused("IAM:Policy:update", {"policy_id": "member", "statements": [
            {"effect": "allow", "actions": ["*"]}]}, org.admin, 404)


class TestRemoving:
    def test_what_was_shared_and_private_data_pass_on_and_private_secrets_go(self, world, org):
        World.install(org, NOTES, JOURNAL)
        note = world.ok("Data:Record:create", {"of": {"agent": "notes", "resource": "notes"},
                                               "fields": {"title": "Mine"}}, org.member)["record"]
        private = world.ok("Secrets:Secret:create", {"of": {"agent": "notes", "resource": "mailbox"},
                                                     "fields": {"server": "a", "password": "p"}}, org.member)["record"]
        shared = world.ok("Secrets:Secret:create", {"of": {"agent": "notes", "resource": "mailbox"},
                                                    "fields": {"server": "b", "password": "p"}}, org.member)["record"]
        world.ok("Secrets:Secret:update", {"record_id": shared["record_id"], "owner": {"users": [org.admin.user_id]}},
                 org.member)
        counts = world.ok("IAM:User:leaving", {"user_id": org.member.user_id}, org.admin)["counts"]
        assert counts["secrets_deleted"] == 1
        world.ok("IAM:User:delete", {"user_id": org.member.user_id}, org.admin)
        assert Records().col.find_one({"_id": note["record_id"]})["created_by"] == org.admin.user_id
        assert Records().col.find_one({"_id": shared["record_id"]})["created_by"] == org.admin.user_id
        assert Records().col.find_one({"_id": private["record_id"]}) is None


class TestNamesAndWords:
    def test_everyone_and_administrators_keep_their_names_and_are_marked(self, world, org):
        groups = world.ok("Account:Profile:peers", {}, org.member)["groups"]
        assert [g["name"] for g in groups if g["administrators"]] == [Groups.ADMINISTRATORS]
        everyone = next(g for g in groups if g["everyone"])
        world.refused("IAM:Group:rename", {"group_id": everyone["group_id"], "name": "All"}, org.admin, 409)

    def test_actions_come_with_their_words(self, world, org):
        catalog = {a["action"]: a["words"] for a in world.ok("Auth:Me:get", {}, org.member)["catalog"]}
        assert catalog["ai:chat:create"] and catalog["records:manage_any"]

    def test_a_new_invitation_ends_the_one_before(self, world, org, mail, monkeypatch):
        monkeypatch.setattr(Links, "recent", lambda self, kind, email: False)
        world.ok("IAM:Invitation:create", {"email": "again@x.test"}, org.admin)
        first = mail[-1]["link"].split("token=")[1]
        world.ok("IAM:Invitation:create", {"email": "again@x.test"}, org.admin)
        world.refused("Auth:Join:accept", {"token": first, "name": "A", "password": PASSWORD}, None, 404)
        second = {"token": mail[-1]["link"].split("token=")[1], "name": "A", "password": PASSWORD}
        assert world.ok("Auth:Join:accept", second)["person"]["email"] == "again@x.test"

    def test_the_trail_is_filtered_by_what_and_how_it_ended(self, world, org):
        world.refused("IAM:User:list", {}, org.member, 403)
        world.ok("Account:Profile:update", {"name": "Bo B"}, org.member)
        lines = world.ok("AI:Audit:list", {"what": "iam:", "outcome": "refused"}, org.member)["lines"]
        assert [line["what"] for line in lines] == ["iam:user:list"]


class TestPolicyNames:
    def test_a_policy_is_renamed_and_two_never_share_a_name(self, world, org):
        made = [world.ok("IAM:Policy:create", {"name": name, "statements": [{"effect": "allow", "actions": ["ai:chat:list"]}]},
                         org.admin)["policy"] for name in ("Readers", "Writers")]
        assert world.ok("IAM:Policy:rename", {"policy_id": made[0]["policy_id"], "name": "Chat readers"},
                        org.admin)["policy"]["name"] == "Chat readers"
        world.refused("IAM:Policy:rename", {"policy_id": made[1]["policy_id"], "name": "Chat readers"}, org.admin, 409)
        world.refused("IAM:Policy:rename", {"policy_id": "member", "name": "Everybody"}, org.admin, 404)
