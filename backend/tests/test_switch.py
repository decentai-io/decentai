"""AI:Switch — the person's own stop for everything of theirs
(api/services/chat_session/halt.py): every chat killed, every key
withdrawn, nothing dialed until they resume."""

import asyncio

from conftest import app_call, signing_key  # noqa: F401
from test_runtime_client import create_chat, fake_runtime  # noqa: F401
from test_runtime_secret_use import runtime_call


def scheduled_chat(anon, admin, seed):
    chat_id = create_chat(admin)
    runtime_call(anon, seed, chat_id, "AI:Schedule:Add", {"row": {
        "schedule_id": "sch_1", "chat_id": chat_id, "mode": "wake",
        "note": "rent", "every_seconds": 86400, "next_run_at": 1.0,
        "enabled": True}})
    return chat_id


def principal(seed):
    return {"org_id": seed.org["_id"], "user_id": seed.admin["_id"],
            "email": seed.admin["email"],
            "assigned_groups": list(seed.admin.get("assigned_groups") or [])}


class TestStopEverything:
    def test_every_chat_is_killed_and_nothing_is_dialed_until_resume(
            self, anon, admin, seed, fake_runtime, signing_key, monkeypatch):
        from api.services.chat_session.halt import Halt
        from api.services.chat_session.relay import RuntimeClientManager
        from database.stores import RuntimeSessionStore
        from server.setup.app_state import get_runtime_clients

        # The stand-in runtime never says a kill is over; do not wait long.
        monkeypatch.setattr(RuntimeClientManager, "KILL_GRACE_SECONDS", 0.2)
        watched = create_chat(admin)
        clocked = scheduled_chat(anon, admin, seed)
        user = principal(seed)
        manager = get_runtime_clients()

        async def scenario():
            # One chat the person has open; the other only keeps a clock.
            assert await manager.ensure(watched, user)
            killed = await Halt().stop(user)
            refused = await manager.ensure(watched, user)
            unsent = await manager.send(
                watched, user, {"event": "user_message", "text": "hello"})
            held = manager.chats_of(user)
            dialed = await Halt().resume(user)
            await manager.close_all()
            return killed, refused, unsent, held, dialed

        killed, refused, unsent, held, dialed = asyncio.run(scenario())
        # Both were killed: the one being watched, and the one nobody was.
        assert killed == 2
        kills = [f for f in fake_runtime.wait_for_frames(2)
                 if f == {"event": "stop", "force": True}]
        assert len(kills) == 2
        # While stopped: no dial, no frame, no socket kept.
        assert (refused, unsent, held) == (False, False, set())
        # Resumed: the chat with a clock is dialed back, by itself.
        assert dialed == 1
        assert RuntimeSessionStore().chats_of(user["user_id"]) == [clocked]

    def test_every_key_is_withdrawn(
            self, anon, admin, seed, fake_runtime, signing_key, monkeypatch):
        """Whatever ignored its end can read nothing: the delegations
        are gone, and none is minted while the person is stopped."""
        from api.services.chat_session.relay import RuntimeClientManager
        from database.stores import RuntimeSessionStore

        monkeypatch.setattr(RuntimeClientManager, "KILL_GRACE_SECONDS", 0.2)
        scheduled_chat(anon, admin, seed)
        stopped = app_call(admin, "AI:Switch:Stop")
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["data"] == {"stopped": True, "chats": 1}
        assert RuntimeSessionStore().chats_of(seed.admin["_id"]) == []
        assert app_call(admin, "AI:Switch:Status").json()["data"] == {
            "stopped": True}
        assert app_call(admin, "AI:Activity:List").json()["data"]["stopped"] is True

        resumed = app_call(admin, "AI:Switch:Resume")
        assert resumed.json()["data"]["stopped"] is False
        assert app_call(admin, "AI:Switch:Status").json()["data"] == {
            "stopped": False}

    def test_what_is_stopped_cannot_start_itself_again(
            self, anon, admin, seed, signing_key):
        """The runtime's delegation has no hand on the switch."""
        chat_id = create_chat(admin)
        for door in ("AI:Switch:Resume", "AI:Switch:Stop", "AI:Switch:Status"):
            assert runtime_call(anon, seed, chat_id, door, {}).status_code == 403

    def test_it_is_the_persons_own(self, admin, seed):
        """Stopping is by person: one who stopped is stopped, and
        nobody else is."""
        from database.stores import UserStore

        users = UserStore()
        other = users.create(seed.org["_id"], "other@test.org", "Other", "x", [])
        users.set_stopped(seed.admin["_id"], True)
        try:
            assert users.stopped(seed.admin["_id"]) is True
            assert users.stopped(other["_id"]) is False
        finally:
            users.set_stopped(seed.admin["_id"], False)
        assert users.stopped(seed.admin["_id"]) is False
