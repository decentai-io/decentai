"""Lists a page at a time: `after` and `limit` asked, `next` and `total` answered, `q` and the filters a page shows
searched by the server, and who may list kept as it was (api-server.md §9)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from conftest import PASSWORD, Runtime
from api_server.chats.store import Cards, Chats, Schedules
from api_server.people.store import Groups, Users
from api_server.runtimes.calls import RuntimeCalls
from api_server.runtimes.store import Refusals, Runtimes


def every_page(world, endpoint, data, caller, key, limit=2):
    """Each page, by the `next` of the one before, until a page says there is none; every row in order."""
    rows, after = [], ""
    for _ in range(50):
        answer = world.ok(endpoint, {**data, "limit": limit, **({"after": after} if after else {})}, caller)
        assert len(answer[key]) <= limit
        rows += answer[key]
        after = answer["next"]
        if after is None:
            return rows, answer
    raise AssertionError("The pages never ended.")


def ids(rows, key):
    return [row[key] for row in rows]


class TestPeople:
    @pytest.fixture
    def people(self, world, org):
        """Five people: the administrator, the member, and three more, one of them an analyst and one disabled."""
        analysts = world.ok("IAM:Group:create", {"name": "Analysts"}, org.admin)["group"]["group_id"]
        made = [Users().create(org.org_id, f"{name.lower()}-{org.org_id}@y.test", name, PASSWORD, groups)
                for name, groups in (("Cy", [analysts]), ("Di", []), ("Ed", []))]
        Users().set(made[2]["_id"], status="disabled")
        return {"analysts": analysts, "made": [m["_id"] for m in made]}

    def test_people_come_a_page_at_a_time_in_the_order_they_were_made(self, world, org, people):
        first = world.ok("IAM:User:list", {"limit": 2}, org.admin)
        assert len(first["users"]) == 2 and first["next"] == first["users"][-1]["user_id"] and first["total"] == 5
        assert first["counts"] == {"people": 5, "administrators": 1, "held": 1}
        assert {g["name"] for g in first["groups"]} == {"Administrators"}
        second = world.ok("IAM:User:list", {"limit": 2, "after": first["next"]}, org.admin)
        assert not {u["user_id"] for u in second["users"]} & {u["user_id"] for u in first["users"]}
        rows, last = every_page(world, "IAM:User:list", {}, org.admin, "users")
        made = [u["_id"] for u in Users().col.find({"org_id": org.org_id}).sort("_id", 1)]
        assert ids(rows, "user_id") == made and last["next"] is None
        whole = world.ok("IAM:User:list", {}, org.admin)
        assert ids(whole["users"], "user_id") == made and whole["next"] is None

    def test_people_are_searched_by_name_email_or_group_and_filtered(self, world, org, people):
        def found(**data):
            return {u["name"] for u in world.ok("IAM:User:list", data, org.admin)["users"]}
        assert found(q="cy") == {"Cy"} and found(q="DI-") == {"Di"} and found(q="analy") == {"Cy"}
        assert found(q="everyone") == set() and found(q="@y.test") == {"Cy", "Di", "Ed"}
        assert found(group_id=people["analysts"]) == {"Cy"}
        assert found(group_id=Groups().everyone(org.org_id)["_id"]) == {"Ada", "Bo", "Cy", "Di", "Ed"}
        assert found(status="held") == {"Ed"} and found(status="active", q="@y.test") == {"Cy", "Di"}
        searched = world.ok("IAM:User:list", {"q": "@y.test", "limit": 1}, org.admin)
        assert searched["total"] == 3 and searched["counts"]["people"] == 5 and searched["next"]
        world.refused("IAM:User:list", {"status": "asleep"}, org.admin, 400)
        world.refused("IAM:User:list", {"group_id": "nothing"}, org.admin, 404)

    def test_only_who_may_list_people_does_and_only_their_own(self, world, org, people):
        world.refused("IAM:User:list", {"limit": 1}, org.member, 403)
        other = world.organization()
        listed = world.ok("IAM:User:list", {"q": "@y.test"}, other.admin)
        assert listed["users"] == [] and listed["total"] == 0 and listed["counts"]["people"] == 2

    def test_invitations_come_a_page_at_a_time_and_are_searched(self, world, org, mail):
        analysts = world.ok("IAM:Group:create", {"name": "Analysts"}, org.admin)["group"]["group_id"]
        for n in range(3):
            world.ok("IAM:Invitation:create", {"email": f"guest{n}@z.test", "groups": [analysts]}, org.admin)
        first = world.ok("IAM:Invitation:list", {"limit": 2}, org.admin)
        assert len(first["invitations"]) == 2 and first["total"] == 3 and first["next"]
        assert first["groups"] == [{"group_id": analysts, "name": "Analysts", "everyone": False, "administrators": False}]
        rows, last = every_page(world, "IAM:Invitation:list", {}, org.admin, "invitations")
        assert sorted(ids(rows, "email")) == ["guest0@z.test", "guest1@z.test", "guest2@z.test"]
        assert len(set(ids(rows, "invitation_id"))) == 3 and last["next"] is None
        searched = world.ok("IAM:Invitation:list", {"q": "GUEST1"}, org.admin)
        assert ids(searched["invitations"], "email") == ["guest1@z.test"] and searched["total"] == 1
        world.refused("IAM:Invitation:list", {}, org.member, 403)


class TestGroupsAndPolicies:
    @pytest.fixture
    def groups(self, world, org):
        policy = world.ok("IAM:Policy:create", {"name": "Readers", "statements": [
            {"effect": "allow", "actions": ["ai:chat:list"]}]}, org.admin)["policy"]["policy_id"]
        made = [world.ok("IAM:Group:create", {"name": name}, org.admin)["group"]["group_id"]
                for name in ("Analysts", "Bakers", "Cooks")]
        world.ok("IAM:Group:set_policies", {"group_id": made[0], "policies": [policy, "member"]}, org.admin)
        world.ok("IAM:User:set_groups", {"user_id": org.member.user_id, "groups": made[:2]}, org.admin)
        return {"made": made, "policy": policy}

    def test_groups_come_a_page_at_a_time_each_counted(self, world, org, groups):
        first = world.ok("IAM:Group:list", {"limit": 2}, org.admin)
        assert ids(first["groups"], "name") == ["Everyone", "Administrators"] and first["total"] == 5
        assert [g["members"] for g in first["groups"]] == [2, 1] and first["counts"] == {"groups": 5, "own": 3}
        rows, last = every_page(world, "IAM:Group:list", {}, org.admin, "groups")
        assert ids(rows, "name") == ["Everyone", "Administrators", "Analysts", "Bakers", "Cooks"]
        assert [g["members"] for g in rows] == [2, 1, 1, 1, 0] and last["next"] is None
        named = world.ok("IAM:Group:list", {"q": "ANA"}, org.admin)
        assert ids(named["groups"], "name") == ["Analysts"] and named["total"] == 1
        assert sorted((p["policy_id"], p["name"]) for p in named["policies"]) == sorted(
            [(groups["policy"], "Readers"), ("member", "Member")])
        chosen = world.ok("IAM:Group:list", {"ids": groups["made"][1:]}, org.admin)
        assert ids(chosen["groups"], "name") == ["Bakers", "Cooks"] and chosen["next"] is None
        world.refused("IAM:Group:list", {}, org.member, 403)

    def test_a_groups_people_come_a_page_at_a_time(self, world, org, groups):
        everyone = Groups().everyone(org.org_id)["_id"]
        rows, last = every_page(world, "IAM:Group:get", {"group_id": everyone}, org.admin, "users", limit=1)
        assert ids(rows, "name") == ["Ada", "Bo"] and last["total"] == 2
        analysts = world.ok("IAM:Group:get", {"group_id": groups["made"][0]}, org.admin)
        assert ids(analysts["users"], "name") == ["Bo"] and analysts["next"] is None and analysts["total"] == 1
        world.refused("IAM:Group:get", {"group_id": everyone}, org.member, 403)

    def test_policies_come_the_built_in_ones_first_a_page_at_a_time(self, world, org, groups):
        for name in ("Writers", "Talkers"):
            world.ok("IAM:Policy:create", {"name": name, "statements": [
                {"effect": "allow", "actions": ["ai:message:list"]}]}, org.admin)
        first = world.ok("IAM:Policy:list", {"limit": 1}, org.admin)
        assert ids(first["policies"], "policy_id") == ["administrator"] and first["next"] == "administrator"
        assert first["total"] == 5 and first["catalog"]
        rows, last = every_page(world, "IAM:Policy:list", {}, org.admin, "policies")
        assert ids(rows, "name") == ["Administrator", "Member", "Readers", "Writers", "Talkers"]
        assert last["next"] is None
        assert ids(every_page(world, "IAM:Policy:list", {}, org.admin, "policies", limit=1)[0], "name") == \
            ids(rows, "name")
        assert ids(world.ok("IAM:Policy:list", {"q": "message:list"}, org.admin)["policies"], "name") == \
            ["Member", "Writers", "Talkers"]
        assert ids(world.ok("IAM:Policy:list", {"q": "read"}, org.admin)["policies"], "name") == ["Readers"]
        chosen = world.ok("IAM:Policy:list", {"ids": [groups["policy"], "member"]}, org.admin)
        assert ids(chosen["policies"], "name") == ["Member", "Readers"]
        world.refused("IAM:Policy:list", {}, org.member, 403)


class TestSettings:
    def test_webhooks_shared_with_a_person_come_a_page_at_a_time(self, world, org):
        own = [world.ok("Settings:Webhook:create", {"name": f"Mine {n}"}, org.member)["webhook"]["webhook_id"]
               for n in range(2)]
        world.ok("Settings:Webhook:update", {"webhook_id": own[0], "enabled": False}, org.member)
        theirs = [world.ok("Settings:Webhook:create", {"name": f"Orders {n}"}, org.admin)["webhook"]["webhook_id"]
                  for n in range(3)]
        for hook in theirs:
            world.ok("Settings:Webhook:share", {"webhook_id": hook, "viewers": {"users": [org.member.user_id]}},
                     org.admin)
        first = world.ok("Settings:Webhook:list", {"limit": 2}, org.member)
        assert ids(first["webhooks"], "webhook_id") == own and len(first["shared"]) == 2 and first["total"] == 3
        assert first["counts"] == {"webhooks": 2, "on": 1, "signed": 0, "shared": 3, "handed": 0}
        rows, last = every_page(world, "Settings:Webhook:list", {}, org.member, "shared")
        assert ids(rows, "webhook_id") == theirs and last["next"] is None
        assert all(row["role"] == "viewer" for row in rows)
        searched = world.ok("Settings:Webhook:list", {"q": "orders 2"}, org.member)
        assert ids(searched["shared"], "webhook_id") == theirs[2:] and searched["webhooks"] == []
        assert searched["total"] == 1
        stranger = world.ok("Settings:Webhook:list", {}, world.organization().member)
        assert stranger["shared"] == [] and stranger["total"] == 0

    def test_model_connections_come_a_page_at_a_time_and_a_member_sees_those_shared(self, world, org):
        everyone = Groups().everyone(org.org_id)["_id"]
        made = [world.ok("Settings:Llm:create", {"name": name, "provider": "openai", "address": "https://x.test/v1",
                                                 "models": models, "groups": groups}, org.admin)["connection"]
                for name, models, groups in (("Main", ["m1", "m2"], [everyone]), ("Spare", [], []),
                                             ("Fast", ["m3"], []))]
        world.ok("Settings:Llm:setdefault", {"connection_id": made[0]["connection_id"]}, org.admin)
        first = world.ok("Settings:Llm:list", {"all": True, "limit": 2}, org.admin)
        assert ids(first["connections"], "name") == ["Main", "Spare"] and first["total"] == 3 and first["next"]
        assert first["counts"] == {"connections": 3, "models": 3, "without_models": 1, "default_name": "Main"}
        rows, last = every_page(world, "Settings:Llm:list", {"all": True}, org.admin, "connections")
        assert ids(rows, "name") == ["Main", "Spare", "Fast"] and last["next"] is None
        assert ids(world.ok("Settings:Llm:list", {"all": True, "q": "m3"}, org.admin)["connections"], "name") == ["Fast"]
        member = world.ok("Settings:Llm:list", {"all": True}, org.member)
        assert ids(member["connections"], "name") == ["Main"] and member["total"] == 1
        assert member["counts"]["connections"] == 1


class TestNodes:
    def test_runtimes_come_a_page_at_a_time_searched_and_by_how_they_stand(self, world, org, monkeypatch):
        own = [Runtime(monkeypatch, scope=org.org_id) for _ in range(4)]
        world.ok("Nodes:Node:update", {"node_id": own[0].peer["id"], "name": "Basement box", "note": "under stairs"},
                 org.admin)
        Runtimes().col.update_one({"_id": own[1].peer["id"]},
                                  {"$set": {"seen_at": Runtimes.now() - timedelta(minutes=5)}})
        Refusals().refuse(own[2].peer["id"], "test", "lost")
        Runtimes().col.update_one({"_id": own[3].peer["id"]}, {"$set": {"sandboxes": [{"sandbox": "sb1"}, {"sandbox": "sb2"}],
                                                                        "drained": ["sb2"]}})
        first = world.ok("Nodes:Node:list", {"limit": 2}, org.admin)
        assert len(first["nodes"]) == 2 and first["total"] == 4 and first["next"]
        assert first["counts"] == {"total": 4, "connected": 2, "refused": 1, "sandboxes": 2, "draining": 1, "chats": 0,
                                   "shared": 0, "own": 4, "own_connected": 2}
        rows, last = every_page(world, "Nodes:Node:list", {}, org.admin, "nodes")
        assert sorted(ids(rows, "node_id")) == sorted(r.peer["id"] for r in own) and last["next"] is None

        def found(**data):
            return {n["node_id"] for n in world.ok("Nodes:Node:list", data, org.admin)["nodes"]}
        assert found(q="basement STAIRS") == {own[0].peer["id"]} and found(q="basement attic") == set()
        assert found(state="connected") == {own[0].peer["id"], own[3].peer["id"]}
        assert found(state="away") == {own[1].peer["id"]} and found(state="refused") == {own[2].peer["id"]}
        assert found(scope="shared") == set() and len(found(scope="own")) == 4
        world.refused("Nodes:Node:list", {"state": "lost"}, org.admin, 400)
        world.refused("Nodes:Node:list", {}, org.member, 403)

    def test_enrollment_tokens_come_a_page_at_a_time_newest_first(self, world, org, monkeypatch):
        scope = {"scope": org.org_id, "labels": ["gpu"]}
        tokens = [{"_id": f"tok_{n}", "kind": "runtime", "metadata": scope, "uses": uses, "expires": None,
                   "withdrawn": withdrawn, "made": 100 - n} for n, (uses, withdrawn) in
                  enumerate([(1, False), (0, False), (None, True), (None, False)])]
        tokens.append({"_id": "tok_other", "kind": "runtime", "metadata": {"scope": "another"}, "uses": 1,
                       "expires": None, "withdrawn": False, "made": 1})

        async def issued(calls, endpoint, data):
            return {"tokens": tokens} if endpoint == "Certificates:Token:list" else \
                {"nodes": [{"_id": "n1", "token": "tok_0"}, {"_id": "n2", "token": "tok_3"}]}
        monkeypatch.setattr(RuntimeCalls, "issuer", issued)
        first = world.ok("Nodes:Authority:list", {"limit": 2}, org.admin)
        assert ids(first["tokens"], "_id") == ["tok_0", "tok_1"] and first["next"] == "tok_1"
        assert first["total"] == 4 and first["counts"] == {"live": 2, "spent": 2} and ids(first["nodes"], "_id") == ["n1"]
        rows, last = every_page(world, "Nodes:Authority:list", {}, org.admin, "tokens")
        assert ids(rows, "_id") == ["tok_0", "tok_1", "tok_2", "tok_3"] and last["next"] is None
        assert ids(world.ok("Nodes:Authority:list", {"state": "live"}, org.admin)["tokens"], "_id") == ["tok_0", "tok_3"]
        assert ids(world.ok("Nodes:Authority:list", {"state": "spent"}, org.admin)["tokens"], "_id") == ["tok_1", "tok_2"]
        assert len(world.ok("Nodes:Authority:list", {"q": "GPU runtime"}, org.admin)["tokens"]) == 4
        assert world.ok("Nodes:Authority:list", {"q": "tok_3"}, org.admin)["total"] == 1
        world.refused("Nodes:Authority:list", {}, org.member, 403)


class TestWhatWaits:
    @pytest.fixture
    def chats(self, world, org):
        return [world.ok("AI:Chat:create", {"title": title}, org.member)["chat"] for title in ("Garden", "Taxes")]

    def test_schedules_come_the_next_to_run_first_a_page_at_a_time(self, world, org, chats):
        def make(chat, prompt, when):
            return world.ok("AI:Schedule:create", {"chat_id": chat["chat_id"], "what": {"prompt": prompt},
                                                   "when": when}, org.member)["schedule"]["schedule_id"]
        later = make(chats[0], "Water the roses", {"every": 7200})
        soon = make(chats[0], "Check the soil", {"every": 600})
        paused = make(chats[1], "File the return", {"every": 60})
        world.ok("AI:Schedule:update", {"schedule_id": paused, "paused": True}, org.member)
        done = make(chats[1], "Pay once", {"at": "2020-01-01T00:00:00+00:00"})
        failing = make(chats[1], "Sum receipts", {"every": 3600})
        Schedules().col.update_one({"_id": failing}, {"$push": {"runs": {"at": Schedules.now(), "failed": True,
                                                                         "summary": "no"}}})
        first = world.ok("AI:Schedule:list", {"limit": 2}, org.member)
        assert ids(first["schedules"], "schedule_id") == [soon, failing] and first["total"] == 5 and first["next"]
        assert first["counts"] == {"all": 5, "active": 2, "failing": 1, "paused": 1, "done": 1}
        assert first["upcoming"]["schedule_id"] == soon
        assert sorted((c["title"], c["count"]) for c in first["chats"]) == [("Garden", 2), ("Taxes", 3)]
        for limit in (1, 2, 3):
            rows, last = every_page(world, "AI:Schedule:list", {}, org.member, "schedules", limit=limit)
            assert ids(rows, "schedule_id") == [soon, failing, later, paused, done] and last["next"] is None

        def found(**data):
            return ids(world.ok("AI:Schedule:list", data, org.member)["schedules"], "schedule_id")
        assert found(state="active") == [soon, later] and found(state="failing") == [failing]
        assert found(state="paused") == [paused] and found(state="done") == [done]
        assert found(chat_id=chats[1]["chat_id"]) == [failing, paused, done]
        assert found(q="ROSES") == [later] and found(q="taxes") == [failing, paused, done]
        assert ids(every_page(world, "AI:Schedule:list", {"chat_id": chats[1]["chat_id"]}, org.member, "schedules",
                              limit=1)[0], "schedule_id") == [failing, paused, done]
        assert world.ok("AI:Schedule:get", {"schedule_id": done}, org.member)["schedule"]["next_at"] is None
        world.refused("AI:Schedule:list", {"after": "nowhere"}, org.member, 400)
        world.refused("AI:Schedule:list", {"state": "asleep"}, org.member, 400)
        world.refused("AI:Schedule:get", {"schedule_id": done}, org.admin, 404)
        theirs = world.ok("AI:Schedule:list", {}, org.admin)
        assert theirs["schedules"] == [] and theirs["counts"]["all"] == 0 and theirs["upcoming"] is None

    def test_open_cards_come_oldest_first_a_page_at_a_time_counted(self, world, org, chats):
        made = [Cards().open(Chats().find(chat["chat_id"]), kind, "c", "notes", {})["_id"]
                for chat, kind in ((chats[0], "approval"), (chats[1], "question"), (chats[0], "approval"))]
        first = world.ok("AI:Activity:list", {"limit": 2}, org.member)
        assert ids(first["cards"], "card_id") == made[:2] and first["next"] == made[1] and first["total"] == 3
        assert first["counts"] == {"approval": 2, "question": 1, "form": 0, "network": 0, "skill": 0, "memory": 0}
        assert first["chats_waiting"] == 2
        assert first["waiting"] == {chats[0]["chat_id"]: 2, chats[1]["chat_id"]: 1}
        assert first["chats"] == {chats[0]["chat_id"]: "Garden", chats[1]["chat_id"]: "Taxes"}
        rows, last = every_page(world, "AI:Activity:list", {}, org.member, "cards")
        assert ids(rows, "card_id") == made and last["next"] is None
        theirs = world.ok("AI:Activity:list", {}, org.admin)
        assert theirs["cards"] == [] and theirs["total"] == 0 and theirs["chats_waiting"] == 0


class TestListsThatPagedAlready:
    def test_skills_say_where_the_next_page_starts(self, world, org):
        for n in range(3):
            world.ok("Skills:Skill:create", {"name": f"Skill {n}", "text": "Do it."}, org.member)
        first = world.ok("Skills:Skill:list", {"limit": 2}, org.member)
        assert first["next"] == first["items"][-1]["id"]
        rows, last = every_page(world, "Skills:Skill:list", {}, org.member, "items")
        assert ids(rows, "name") == ["Skill 0", "Skill 1", "Skill 2"] and last["next"] is None
        assert world.ok("Skills:Skill:list", {"limit": 3}, org.member)["next"] is None
