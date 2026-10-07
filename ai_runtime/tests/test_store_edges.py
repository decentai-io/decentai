"""The code store, the environments, the proxy and the event log, at
their edges: an install is all of itself or nothing, and what many do
at once does not undo what each of them does.
"""

import asyncio
import threading

import pytest

from ai_runtime.agents.egress import EgressProxy, Refused
from ai_runtime.agents.environments import READY_MARKER, AgentEnvironment
from ai_runtime.agents.events import Events
from ai_runtime.agents.library import AgentLibrary, AgentRefused
from contracts.agent_package import AgentPackage
from ai_runtime.tests.fixture_agents import write_agent


@pytest.fixture()
def library(tmp_path):
    return AgentLibrary(tmp_path / "runtime")


def packaged(tmp_path):
    folder = write_agent(tmp_path / "source", "demo")
    return AgentPackage.build(folder)


class TestAnInstallIsAllOrNothing:
    def test_a_digest_that_is_not_one_is_refused_in_words(self, library):
        with pytest.raises(AgentRefused):
            library.install("sha256:not-a-digest", b"")

    def test_an_agent_being_installed_is_not_handed_out(
            self, library, tmp_path):
        archive, digest = packaged(tmp_path)
        seen = []
        proving = library._approved

        def while_it_is_proved(*args, **kwargs):
            # The folder has arrived and nothing about it is proven.
            seen.append((library.has(digest), library.agent(digest)))
            return proving(*args, **kwargs)
        library._approved = while_it_is_proved
        library._verify = lambda *args, **kwargs: []
        AgentEnvironment.runner = staticmethod(lambda argv: (0, ""))
        try:
            library.install(digest, archive)
        except AgentRefused:
            pass
        finally:
            AgentEnvironment.runner = None
        assert seen == [(True, None)]

    def test_an_error_nobody_expected_leaves_nothing_behind(
            self, library, tmp_path):
        archive, digest = packaged(tmp_path)

        def breaks(*args, **kwargs):
            raise RuntimeError("a disk path that should not be shown")
        library._approved = breaks
        with pytest.raises(AgentRefused) as refused:
            library.install(digest, archive)
        assert "disk path" not in str(refused.value)
        assert not library.has(digest) and library.agent(digest) is None

    def test_one_unreadable_folder_does_not_stop_the_start(
            self, library, tmp_path):
        archive, digest = packaged(tmp_path)
        assert library._materialize(digest, archive) == []

        def breaks(*args, **kwargs):
            raise TypeError("a manifest of the wrong shape")
        library._register = breaks
        library.load_all()
        assert library.loaded() == {}


class TestTheSdkIsRefreshedOnce:
    def test_two_at_once_leave_it_whole(self, tmp_path):
        root = tmp_path / "env"
        (root / "Lib" / "site-packages").mkdir(parents=True)
        (root / READY_MARKER).write_text("", encoding="utf-8")
        problems = []

        def refresh():
            (root / AgentEnvironment.SDK_MARKER).write_text(
                "stale", encoding="utf-8")
            problems.extend(AgentEnvironment(root).refresh_sdk())

        for _ in range(5):
            threads = [threading.Thread(target=refresh) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        assert problems == []
        placed = root / "Lib" / "site-packages" / "decentai_sdk"
        assert (placed / "worker.py").is_file()
        assert sorted(p.name for p in placed.parent.iterdir()) == ["decentai_sdk"]


class TestEveryEventIsOnSomePage:
    def test_events_of_one_instant_are_not_lost_between_pages(self, tmp_path):
        before = Events.current
        Events.configure(tmp_path, "runtime")
        try:
            for number in range(10):
                Events.record("log", line=str(number))
        finally:
            Events.current = before
        read, edge = [], None
        while True:
            page = Events.read(tmp_path, limit=4, before=edge)
            if not page:
                break
            read.extend(event["line"] for event in page)
            edge = page[-1]["at"]
        assert sorted(read, key=int) == [str(number) for number in range(10)]


class TestTheProxy:
    def test_brackets_that_hold_no_address_are_a_refusal(self):
        for target in ("[abc:443", "[zz]:443"):
            with pytest.raises(Refused):
                EgressProxy._where("CONNECT", target)

    def test_the_longest_silent_connection_makes_room(self):
        """Connections that have said nothing are counted too: past
        the number, the one silent for longest is closed."""

        class Writer:
            def __init__(self, reader):
                self.reader, self.closed = reader, False

            def close(self):
                self.closed = True
                self.reader.feed_eof()

        async def scenario():
            proxy = EgressProxy(0)
            proxy.MAX_UNREAD = 2
            writers, serving = [], []
            for _ in range(3):
                reader = asyncio.StreamReader()
                writers.append(Writer(reader))
                serving.append(asyncio.ensure_future(
                    proxy._connection(reader, writers[-1])))
                await asyncio.sleep(0)
            await asyncio.sleep(0.05)
            closed = [writer.closed for writer in writers]
            for writer in writers:
                writer.close()
            await asyncio.gather(*serving)
            return closed
        assert asyncio.run(scenario()) == [True, False, False]
