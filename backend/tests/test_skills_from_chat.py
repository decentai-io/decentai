"""A skill saved from a chat (save_skill; docs/system/data-layer.md,
Skills).

The runtime only opens the card — under the chat's delegation, which
carries the person's own policy — and the person's save creates the
skill, as them, from the words the card holds. What these pin: the card
is refused to somebody without skills:skill:create and for a title
already taken; the record keeps the skill whole; a decline keeps
nothing; a save makes a skill owned by the person alone, on the trail
as made from that chat; the grant is asked again at the save; and the
runtime still cannot write a skill by itself.
"""

import pytest

from conftest import app_call
from test_ai_messages import make_chat
from test_data_layer import _group, _user
from test_runtime_secret_use import signing_key  # noqa: F401

SKILL = {
    "title": "Jira bulk create and resolve",
    "summary": "When many Jira tickets are to be created and resolved at once.",
    "body": "1. Open the board.\n2. Run:\n\n```javascript\n"
            "await fetch('/rest/api/2/issue', {method: 'POST'});\n```",
}

#: What a member needs for the chat itself, and nothing about skills
#: beyond the baseline's reading.
CHAT_ACTIONS = ["ai:chat:*", "ai:message:*", "ai:approval:*"]


def delegated(anon, user_doc, chat_id, endpoint, data):
    """One /app call as the delegated runtime of ``user_doc``'s chat."""
    from api.services.chat_session.identity import Delegation

    token = Delegation().for_chat({
        "user_id": user_doc["_id"], "org_id": user_doc["org_id"],
        "email": user_doc["email"], "session_id": "",
    }, chat_id)
    return anon.post("/app", json={
        "endpoint": endpoint, "data": {"chat_id": chat_id, **data},
    }, headers={"Authorization": f"Bearer {token}"})


def offer(anon, user_doc, chat_id, **skill):
    return delegated(anon, user_doc, chat_id, "AI:Approval:Open", {
        "request": {"kind": "question", "function": "save_skill",
                    "agent_id": "", "agent_name": "",
                    "question": "Save this as a skill?", "choices": [],
                    "expects": "skill", "skill": {**SKILL, **skill}}})


@pytest.fixture()
def author(app, admin, seed):
    """A member who may chat and create skills."""
    group = _group(admin, "Authors", [*CHAT_ACTIONS, "skills:skill:create"])
    return _user(app, seed, "author@test.org", [group])


@pytest.fixture()
def reader(app, admin, seed):
    """A member who may chat and read skills, and not create them."""
    group = _group(admin, "Readers", CHAT_ACTIONS)
    return _user(app, seed, "reader@test.org", [group])


def skill_docs():
    from database.stores.data.skills import SkillStore
    return list(SkillStore().col.find({"type": "skill"}))


class TestTheCard:
    def test_without_the_grant_no_card_is_opened(
            self, anon, reader, signing_key):
        client, doc = reader
        chat_id = make_chat(client)
        refused = offer(anon, doc, chat_id)
        assert refused.status_code == 403, refused.text
        assert "skills:skill:create" in refused.json()["error"]["message"]
        assert app_call(client, "AI:Approval:List", {
            "chat_id": chat_id}).json()["data"]["approvals"] == []

    def test_a_card_keeps_the_skill_whole_and_refuses_a_broken_one(
            self, anon, author, signing_key):
        client, doc = author
        chat_id = make_chat(client)
        for broken in ({"title": ""}, {"summary": "   "},
                       {"body": "x" * 20_001}, {"anything": "else"}):
            assert offer(anon, doc, chat_id, **broken).status_code == 400, broken
        opened = offer(anon, doc, chat_id)
        assert opened.status_code == 200, opened.text
        [card] = app_call(client, "AI:Approval:List", {
            "chat_id": chat_id}).json()["data"]["approvals"]
        assert card["request"]["expects"] == "skill"
        assert card["request"]["skill"] == SKILL

    def test_a_title_already_taken_is_refused(self, anon, author, signing_key):
        client, doc = author
        assert app_call(client, "Skills:Skill:Create", SKILL).status_code == 200
        chat_id = make_chat(client)
        refused = offer(anon, doc, chat_id, title="  JIRA bulk create and resolve")
        assert refused.status_code == 409

    def test_a_helper_thread_offers_none(self, anon, author, signing_key):
        client, doc = author
        chat_id = make_chat(client)
        refused = delegated(anon, doc, chat_id, "AI:Approval:Open", {
            "thread": "sub_0123abcd",
            "request": {"kind": "question", "function": "save_skill",
                        "question": "Save?", "choices": [],
                        "expects": "skill", "skill": SKILL}})
        assert refused.status_code == 400


class TestTheDecision:
    def test_a_decline_keeps_nothing(self, anon, author, signing_key):
        client, doc = author
        chat_id = make_chat(client)
        approval_id = offer(anon, doc, chat_id).json()["data"]["approval_id"]
        assert app_call(client, "AI:Approval:Decide", {
            "approval_id": approval_id, "answer": "yes please",
        }).status_code == 400
        declined = app_call(client, "AI:Approval:Decide", {
            "approval_id": approval_id, "answer": "decline"})
        assert declined.status_code == 200, declined.text
        assert skill_docs() == []
        from database.stores import ApprovalStore
        record = ApprovalStore().in_chat(approval_id, chat_id)
        assert (record["answer"], record["answer_label"]) == ("decline", "Not saved")

    def test_a_save_is_the_persons_own_private_skill_on_the_trail(
            self, anon, admin, author, signing_key):
        client, doc = author
        chat_id = make_chat(client)
        approval_id = offer(anon, doc, chat_id).json()["data"]["approval_id"]
        saved = app_call(client, "AI:Approval:Decide", {
            "approval_id": approval_id, "answer": "save"})
        assert saved.status_code == 200, saved.text
        row = saved.json()["data"]["skill"]
        assert row["title"] == SKILL["title"]

        [stored] = skill_docs()
        assert stored["_id"] == row["ref"]
        assert stored["created_by"] == doc["_id"]
        # Private: theirs alone, until they share it.
        assert stored["owner"]["users"] == [doc["_id"]]
        assert not any(v for k, v in stored["owner"].items() if k != "users")
        got = app_call(client, "Skills:Skill:Get", {"resource_ref": row["ref"]})
        assert got.json()["resource"]["values"]["body"] == SKILL["body"]
        # Listed for the chat's runtime: the next turn's SKILLS.
        listed = delegated(anon, doc, chat_id, "Skills:Skill:List", {})
        assert row["ref"] in {r["resource_ref"] for r in listed.json()["resources"]}

        from database.stores import AuditStore
        [line] = list(AuditStore().col.find({"event_type": "skill.created"}))
        assert line["user_id"] == doc["_id"] and line["chat_id"] == chat_id
        assert line["resource_refs"] == [row["ref"], approval_id]
        assert line["details"] == {"source": "chat",
                                   "body_chars": len(SKILL["body"])}
        assert SKILL["body"] not in str(line)

        # Once: a second save is refused, and nothing more is made.
        assert app_call(client, "AI:Approval:Decide", {
            "approval_id": approval_id, "answer": "save",
        }).status_code == 409
        assert len(skill_docs()) == 1

    def test_the_grant_is_asked_again_at_the_save(
            self, app, anon, admin, seed, signing_key):
        """Withdrawn while the card waited: the save is refused with the
        reason and the card stays open, for the person to decline."""
        from database.stores import ApprovalStore, UserStore, access_cache

        chatting = _group(admin, "Chatting", CHAT_ACTIONS)
        creating = _group(admin, "Creating", ["skills:skill:create"])
        client, doc = _user(app, seed, "both@test.org", [chatting, creating])
        chat_id = make_chat(client)
        approval_id = offer(anon, doc, chat_id).json()["data"]["approval_id"]
        UserStore().col.update_one({"_id": doc["_id"]},
                                   {"$set": {"assigned_groups": [chatting]}})
        access_cache.clear()
        refused = app_call(client, "AI:Approval:Decide", {
            "approval_id": approval_id, "answer": "save"})
        assert refused.status_code == 403, refused.text
        assert "skills:skill:create" in refused.json()["error"]["message"]
        assert skill_docs() == []
        assert ApprovalStore().in_chat(approval_id, chat_id)["status"] == "pending"
        assert app_call(client, "AI:Approval:Decide", {
            "approval_id": approval_id, "answer": "decline",
        }).status_code == 200

    def test_a_chat_narrowed_to_some_skills_gains_the_one_saved_in_it(
            self, anon, author, signing_key):
        client, doc = author
        other = app_call(client, "Skills:Skill:Create", {
            **SKILL, "title": "Style guide"}).json()["resource"]["resource_ref"]
        chat_id = make_chat(client)
        assert app_call(client, "AI:Chat:Update", {
            "chat_id": chat_id, "config": {"enabled_skills": [other]},
        }).status_code == 200
        approval_id = offer(anon, doc, chat_id).json()["data"]["approval_id"]
        ref = app_call(client, "AI:Approval:Decide", {
            "approval_id": approval_id, "answer": "save",
        }).json()["data"]["skill"]["ref"]
        from database.stores import ChatStore
        chosen = ChatStore().by_chat_id(chat_id)["config"]["enabled_skills"]
        assert chosen == [other, ref]


class TestTheFence:
    def test_the_runtime_still_cannot_create_a_skill_itself(
            self, anon, author, signing_key):
        client, doc = author
        chat_id = make_chat(client)
        refused = delegated(anon, doc, chat_id, "Skills:Skill:Create", SKILL)
        assert refused.status_code == 403
        assert skill_docs() == []

    def test_a_skill_made_on_the_page_is_on_the_trail_too(self, author):
        client, doc = author
        made = app_call(client, "Skills:Skill:Create", SKILL)
        ref = made.json()["resource"]["resource_ref"]
        from database.stores import AuditStore
        [line] = list(AuditStore().col.find({"event_type": "skill.created"}))
        assert line["resource_refs"] == [ref] and line["chat_id"] is None
        assert line["details"]["source"] == "page"
