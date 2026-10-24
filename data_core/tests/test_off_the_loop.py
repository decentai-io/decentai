"""The database waited on beside the loop, never on it
(docs/system/cluster.md#the-database).

The Data Core is one loop per process: every person's socket, every
node's link and the gateway's requests are served on it. The database
is called synchronously, so a call made on the loop holds all of them
for as long as the database takes. What is pinned: the gateway's own
checks — who is asking, a node's admission, a lease's number, the
caller's policies, the audit trail — and a chat's attachment wait on a
thread, so a slow database slows the request that waits on it and
nothing else.
"""

import asyncio
import time

import httpx

from conftest import signing_key  # noqa: F401
from test_runtime_client import FakeNode, create_chat, link, principal

#: How long the database is made to take for one call.
SLOW = 0.4


async def lag_while(work, every=0.02):
    """The longest the loop was held while ``work`` ran."""
    longest = 0.0
    done = asyncio.Event()

    async def ticking():
        nonlocal longest
        while not done.is_set():
            before = time.monotonic()
            await asyncio.sleep(every)
            longest = max(longest, time.monotonic() - before - every)

    ticker = asyncio.create_task(ticking())
    try:
        return await work, longest
    finally:
        done.set()
        await ticker


class TestTheGateway:
    def test_slow_sign_ins_at_once_take_the_time_of_one_and_hold_up_nobody(
            self, app, seed, monkeypatch):
        from server.authentication.access import AccessController

        def slowly(self, request):
            time.sleep(SLOW)            # the database, taking its time
            return None

        monkeypatch.setattr(AccessController, "verify_request_auth", slowly)

        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://data-core") as client:
                began = time.monotonic()
                answers, lag = await lag_while(asyncio.gather(*(
                    client.post("/app", json={"endpoint": "AI:Chat:List", "data": {}})
                    for _ in range(8))))
                return [answer.status_code for answer in answers], \
                    time.monotonic() - began, lag

        statuses, took, lag = asyncio.run(scenario())
        assert statuses == [401] * 8
        # Side by side: about one call's time, not eight.
        assert took < SLOW * 4
        assert lag < SLOW / 2


class TestAnAttachment:
    def test_a_slow_lease_write_does_not_hold_the_loop(
            self, admin, seed, signing_key, monkeypatch):
        from database.stores import LeaseStore
        from server.setup.app_state import get_runtime_clients

        link(FakeNode("auth_a/slow", greet=False))
        chat_id = create_chat(admin)
        grant = LeaseStore.grant

        def slowly(self, *args, **kwargs):
            time.sleep(SLOW)
            return grant(self, *args, **kwargs)

        monkeypatch.setattr(LeaseStore, "grant", slowly)

        async def scenario():
            return await lag_while(get_runtime_clients().hold(chat_id, principal(seed)))

        held, lag = asyncio.run(scenario())
        assert held is True
        assert lag < SLOW / 2
