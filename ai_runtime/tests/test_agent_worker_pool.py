"""The pool's two isolation rules, without real processes: a worker per
approved agent, and an ask answered only from the worker its invocation
was sent to. The protocol itself is proven in test_agent_worker.py and
the handle in test_agent_worker_handle.py; this is the bookkeeping
above them, which is where one invocation's authority would leak into
another's if it leaked anywhere.
"""

import asyncio
from types import SimpleNamespace

import pytest

import ai_runtime.agents.pool as pool_module
from ai_runtime.agents.workers import WorkerError
from ai_runtime.agents.pool import WorkerPool
from ai_runtime.execution.call import Call
from ai_runtime.sinks import ChatSinks
from ai_runtime.tests.fake_container import confinement
from ai_runtime.tests import fake_container

DIGEST = "sha256:" + "0" * 64


def run(awaitable):
    return fake_container.run(awaitable)


async def current_sdk():
    """An environment whose SDK needs no refreshing."""
    return []


def approved(ref: str):
    """An approved agent as the pool sees it: two refs, one package."""
    return SimpleNamespace(
        agent_id=ref, digest=DIGEST, folder=".",
        environment=SimpleNamespace(python="python", refresh_sdk=current_sdk),
        manifest=SimpleNamespace(document={}),
    )


class FakeHandle:
    """A handle that never spawns: alive on start, answers every invoke
    — or holds one forever when the function is ``hang``."""

    GRACE_SECONDS = 1

    def __init__(self, python, folder, manifest_document, place=None):
        self.place = place
        self.conversations = []
        self.router = None
        self.progress = None
        self.process = None
        self.alive = True
        self.cancelled = []

    async def start(self):
        return []

    async def invoke(self, call_id, function, inputs, conversation=""):
        self.conversations.append(conversation)
        if function == "hang":
            await asyncio.Event().wait()
        if function == "use_secret":
            answer = await self.router(
                "resources.use_secret",
                {"call_id": call_id, "resource_id": "microsoft"})
            return answer, "success"
        return {"ok": True}, "success"

    async def cancel(self, call_id):
        self.cancelled.append(call_id)
        return True

    async def stop(self):
        self.alive = False


@pytest.fixture
def pool(monkeypatch):
    monkeypatch.setattr(pool_module, "WorkerHandle", FakeHandle)
    return WorkerPool(confinement())


class Place:
    """Stands for a confined worker's place: what a credential opened,
    and what was taken back."""

    def __init__(self):
        self.open = []
        self.seen_open = []

    def block(self, names):
        pass

    def reached(self):
        return {}

    def learn(self, secret_id, credential):
        lent = [(credential["host"], None)]
        self.open.extend(lent)
        self.seen_open.append(list(self.open))
        return lent

    def take_back(self, lent):
        for one in lent:
            self.open.remove(one)


class TestAHostACredentialNames:
    """``from_secret``: the host a credential names is open for the call
    that was handed the credential, and closed when that call ends — a
    worker serves every person of an organization."""

    def test_it_closes_when_the_call_ends(self, pool, monkeypatch):
        place = Place()
        monkeypatch.setattr(pool.confinement, "place", lambda key: place)

        async def use_secret(resource_id, ref):
            return {"host": "mail.sara.example"}

        async def scenario():
            saras = Call("c_1", resources=SimpleNamespace(use_secret=use_secret))
            await pool.invoke(approved("agt_a"), "use_secret", {}, saras, 5)
            assert place.seen_open == [[("mail.sara.example", None)]]
            assert place.open == [] and saras.learned == []
            # The same worker, the next person's call: nothing is open.
            await pool.invoke(approved("agt_a"), "notebook.note.find", {}, Call("c_2"), 5)
            assert place.open == []
        run(scenario())

    def test_it_closes_when_the_call_fails(self, pool, monkeypatch):
        place = Place()
        monkeypatch.setattr(pool.confinement, "place", lambda key: place)
        context = Call("c_1")
        context.learned = [("mail.sara.example", None)]
        place.open = list(context.learned)

        async def scenario():
            with pytest.raises(asyncio.TimeoutError):
                await pool.invoke(approved("agt_a"), "hang", {}, context, 0.05)
            assert place.open == []
        run(scenario())


class TestIsolation:
    def test_two_approvals_of_one_package_get_two_workers(self, pool):
        a, b = approved("agt_a"), approved("agt_b")
        assert a.digest == b.digest

        async def scenario():
            await pool.invoke(a, "notebook.note.find", {}, Call("c_1"), 5)
            await pool.invoke(b, "notebook.note.find", {}, Call("c_2"), 5)
            await pool.invoke(a, "notebook.note.find", {}, Call("c_3"), 5, conversation="chat_9")
            assert set(pool._handles) == {"agt_a", "agt_b"}
            assert pool._handles["agt_a"] is not pool._handles["agt_b"]
            # The chat's key rides along to the worker, absent outside a chat.
            assert pool._handles["agt_a"].conversations == ["", "chat_9"]
        run(scenario())

    def test_an_update_retires_the_worker_running_the_version_before(self, pool):
        before = approved("agt_a")
        after = approved("agt_a")
        after.digest = "sha256:" + "1" * 64

        async def scenario():
            await pool.invoke(before, "expenses.claims.get", {}, Call("c_1"), 5)
            old = pool._handles["agt_a"]
            await pool.invoke(after, "expenses.claims.list", {}, Call("c_2"), 5)
            new = pool._handles["agt_a"]
            assert new is not old and not old.alive and new.alive
            assert pool.live_digests() == {after.digest}
            # The same version again keeps its worker.
            await pool.invoke(after, "expenses.claims.list", {}, Call("c_3"), 5)
            assert pool._handles["agt_a"] is new
        run(scenario())

    def test_an_ask_is_answered_only_from_the_worker_it_was_sent_to(self, pool):
        a, b = approved("agt_a"), approved("agt_b")
        rows = [{"title": "private"}]

        async def list_data(resource_id, filters):
            return rows

        async def scenario():
            await pool.invoke(a, "notebook.note.find", {}, Call("c_0"), 5)
            await pool.invoke(b, "notebook.note.find", {}, Call("c_0"), 5)
            worker_a, worker_b = pool._handles["agt_a"], pool._handles["agt_b"]

            # One live invocation, sent to worker A.
            context = Call("c_live", resources=SimpleNamespace(list_data=list_data))
            context.handle = worker_a
            pool._calls["c_live"] = context
            ask = {"call_id": "c_live", "resource_id": "note", "filters": None}

            assert await worker_a.router("resources.list_data", ask) == rows
            # The same id from another worker — however it learned it —
            # opens nothing.
            with pytest.raises(WorkerError, match="not entitled|entitled"):
                await worker_b.router("resources.list_data", ask)
            # Nor does an id nobody holds.
            with pytest.raises(WorkerError):
                await worker_a.router("resources.list_data",
                                      {**ask, "call_id": "c_unknown"})
        run(scenario())

    def test_cancelling_the_invocation_reaches_the_worker(self, pool):
        # The platform cancels a job by cancelling its task. The order
        # must travel to the worker, or the function runs on with the
        # job already reported cancelled.
        a = approved("agt_a")

        async def scenario():
            invocation = asyncio.create_task(pool.invoke(
                a, "hang", {}, Call("c_hang"), 60))
            await asyncio.sleep(0.05)              # let it reach the worker
            invocation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await invocation
            assert pool._handles["agt_a"].cancelled == ["c_hang"]
            assert "c_hang" not in pool._calls
        run(scenario())

    def test_progress_is_forwarded_only_from_the_owning_worker(self, pool):
        a, b = approved("agt_a"), approved("agt_b")
        heard = []

        async def sink(description, source):
            heard.append(description)

        async def scenario():
            await pool.invoke(a, "f", {}, Call("c_0"), 5)
            await pool.invoke(b, "f", {}, Call("c_0"), 5)
            context = Call("c_live", sinks=ChatSinks(progress=sink))
            context.handle = pool._handles["agt_a"]
            pool._calls["c_live"] = context

            await pool._handles["agt_b"].progress("c_live", "from b")
            await pool._handles["agt_a"].progress("c_live", "from a")
            assert heard == ["from a"]
        run(scenario())


class TestAskContext:
    def test_an_ask_acts_for_the_chat_that_invoked_not_the_one_that_spawned(self, pool):
        # A worker outlives the chat that started it. Its asks must reach
        # the platform as the chat calling now: each call carries the
        # resources of its own chat, and the pool answers an ask from
        # the call's context and nothing else.
        a = approved("agt_a")

        def resources_of(chat_id):
            async def use_secret(resource_id, ref=None):
                return {"chat": chat_id}
            return SimpleNamespace(use_secret=use_secret)

        async def in_chat(chat_id, call_id):
            context = Call(call_id, resources=resources_of(chat_id))
            return await pool.invoke(a, "use_secret", {}, context, 5)

        async def scenario():
            first = await in_chat("chat_a", "c_1")
            second = await in_chat("chat_b", "c_2")
            assert first == ({"chat": "chat_a"}, "success")
            assert second == ({"chat": "chat_b"}, "success")
        run(scenario())
