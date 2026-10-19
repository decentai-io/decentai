"""What used to leave no line on the trail now leaves one: a person
stopping a chat, and the assistant putting a schedule on the clock or
taking it off. A line the database will not take is not only lost: it
is tried again and kept whole in the error log."""

from conftest import app_call
from test_ai_audit import events_for
from test_ai_messages import make_chat
from test_runtime_secret_use import runtime_call, signing_key  # noqa: F401


def test_stopping_a_chat_is_on_its_trail(admin):
    chat_id = make_chat(admin, "stopped")
    for force in (False, True):
        stopped = app_call(admin, "AI:Chat:Stop",
                           {"chat_id": chat_id, "force": force})
        assert stopped.status_code == 200, stopped.text
    lines = [e for e in events_for(admin, chat_id)
             if e["event_type"] == "chat.stopped"]
    assert sorted(line["details"]["force"] for line in lines) == [False, True]


def test_the_assistants_schedules_are_on_the_trail(
        anon, admin, seed, signing_key):
    chat_id = make_chat(admin, "clock")
    row = {"schedule_id": "sch_trail", "chat_id": chat_id, "mode": "wake",
           "note": "invoices", "every_seconds": 0, "cron": "",
           "next_run_at": 1.0, "enabled": True, "last_run_at": None,
           "runs": []}
    assert runtime_call(anon, seed, chat_id, "AI:Schedule:Add",
                        {"row": row}).status_code == 200
    assert runtime_call(anon, seed, chat_id, "AI:Schedule:Remove",
                        {"schedule_id": "sch_trail"}).status_code == 200

    lines = {e["event_type"]: e for e in events_for(admin, chat_id)
             if e["event_type"].startswith("schedule.")}
    assert lines["schedule.created"]["details"]["by"] == "assistant"
    assert lines["schedule.deleted"]["details"]["by"] == "assistant"


def test_a_line_the_database_refuses_is_tried_again_and_kept(seed):
    from database.stores import AuditStore

    store = AuditStore()
    tried = []

    class Refusing:
        def insert_one(self, doc):
            tried.append(doc)
            raise RuntimeError("database away")

    type(store).col = property(lambda self: Refusing())
    try:
        kept = store.append("chat.stopped", {"org_id": "o", "user_id": "u"},
                            chat_id="c", details={"force": True})
    finally:
        del type(store).col
    assert kept is False
    assert len(tried) == 2
    assert tried[0]["event_type"] == "chat.stopped"
