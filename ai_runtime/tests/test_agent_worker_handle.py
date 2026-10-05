"""The host side of the worker protocol: WorkerHandle and the install
verification probe, against real environments.

One real venv is built per test run (session fixture) and every handle
test spawns real workers from it with ``-I`` — the isolation being
tested must be the isolation being used.
"""

import os
import asyncio
from pathlib import Path

import pytest
import yaml

from ai_runtime.agents.environments import AgentEnvironment
from ai_runtime.agents.library import AgentLibrary, AgentRefused
from ai_runtime.agents.worker_handle import WorkerError, WorkerHandle
from contracts.agent_manifest import manifest_hash
from contracts.agent_package import AgentPackage

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "agents" / "notebook"


def run(awaitable):
    return asyncio.run(awaitable)


def manifest_document():
    with open(FIXTURE / "manifest.yaml", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture(scope="session")
def venv(tmp_path_factory):
    """One real environment for the whole run — the notebook fixture
    declares no dependencies, so this is venv + SDK copy only."""
    env = AgentEnvironment(tmp_path_factory.mktemp("envs") / "notebook")
    assert env.build([]) == []
    return env


class TestWorkerHandle:
    def test_start_handshakes_and_stop_shuts_down(self, venv):
        async def scenario():
            handle = WorkerHandle(venv.python, FIXTURE, manifest_document())
            assert await handle.start() == []
            assert handle.alive
            await handle.stop()
            assert not handle.alive
        run(scenario())

    def test_start_reports_a_broken_package(self, venv):
        async def scenario():
            document = manifest_document()
            document["implementation"]["entrypoint"] = "agent:Nope"
            handle = WorkerHandle(venv.python, FIXTURE, document)
            errors = await handle.start()
            assert errors and "'Nope' not found" in errors[0]
            assert not handle.alive          # a refused worker is not kept
        run(scenario())

    def test_invoke_routes_asks_and_progress_through_the_host(self, venv):
        """A full invocation over the real wire: the function's resource
        ask reaches the router, its progress reaches the callback, and
        its result comes back typed."""
        async def scenario():
            handle = WorkerHandle(venv.python, FIXTURE, manifest_document())
            seen = {"asks": [], "progress": []}

            async def router(method, params):
                seen["asks"].append((method, params))
                assert params["call_id"] == "c_1"
                return {"resource_ref": "rec_7"}

            async def progress(call_id, description):
                seen["progress"].append((call_id, description))

            handle.router = router
            handle.progress = progress
            assert await handle.start() == []
            try:
                result, status = await handle.invoke(
                    "c_1", "notebook.note.save",
                    {"notebook": "Work", "title": "Handoff"},
                )
                assert status == "success"
                assert result == {"note_ref": "rec_7", "created": True}
                assert seen["asks"][0][0] == "resources.create_data"
                assert ("c_1",) == tuple(p[0] for p in seen["progress"])
            finally:
                await handle.stop()
        run(scenario())

    def test_bytes_too_large_for_the_line_travel_through_the_spool(self, venv, tmp_path):
        """A scan runs to megabytes and a line is capped. A read that
        large reaches the worker as a path in the spool, a create that
        large comes back as one, and agent code sees only bytes: the
        same read_file / create_file it always called. The spool is
        clean after the call and gone after the worker."""
        import base64
        import os
        import textwrap
        from ai_runtime.tests.fixture_agents import MINIMAL_MANIFEST, write_agent

        write_agent(tmp_path, "spooler", manifest=MINIMAL_MANIFEST.format(
            agent_id="spooler", entrypoint="agent:DemoAgent"),
            files={"agent.py": textwrap.dedent("""                import base64
                from decentai_sdk.base import AgentBase, ToolBase

                class MainTool(ToolBase):
                    id = "main"

                    async def run(self, call):
                        record = await call.resources.read_file("scan", "f1")
                        raw = base64.b64decode(record["content_base64"])
                        created = await call.resources.create_file(
                            "scan", "copy.bin", content_base64=record["content_base64"])
                        return {"size": len(raw), "head": raw[:4].decode("latin-1"),
                                "text_too": "content" in record,
                                "created": created}, "success"

                class DemoAgent(AgentBase):
                    def tools(self):
                        return [MainTool(self)]
            """)})
        document = yaml.safe_load(
            (tmp_path / "spooler" / "manifest.yaml").read_text(encoding="utf-8"))
        payload = b"%PDF" + os.urandom(3 * 1024 * 1024)      # 3 MiB, past any line
        encoded = base64.b64encode(payload).decode("ascii")
        seen = {}

        async def scenario():
            handle = WorkerHandle(venv.python, tmp_path / "spooler", document)

            async def router(method, params):
                if method == "resources.read_file":
                    return {"filename": "f1.bin", "file_type": "application/pdf",
                            "content_base64": encoded}
                if method == "resources.create_file":
                    # The handle unspooled it: the router sees bytes, not a path.
                    seen["create"] = dict(params)
                    return {"resource_ref": "fil_copy"}
                raise AssertionError(method)

            handle.router = router
            assert await handle.start() == []
            spool = handle.spool
            assert spool is not None and spool.is_dir()
            try:
                result, status = await handle.invoke("c_1", "spooler.main.run", {})
                assert status == "success", result
                assert (result["size"], result["head"]) == (len(payload), "%PDF")
                assert result["text_too"] is False        # not text: no content
                assert result["created"] == {"resource_ref": "fil_copy"}
                assert "content_path" not in seen["create"]
                assert seen["create"]["content_base64"] == encoded
                assert list(spool.iterdir()) == []       # both files consumed
            finally:
                await handle.stop()
            assert not spool.exists()

        run(scenario())

    def test_a_path_outside_the_spool_is_refused(self, venv, tmp_path):
        """The worker names a file to create by path; only the spool
        counts. Anything else is refused as a resource ask is, and the
        function hears why."""
        import textwrap
        from ai_runtime.tests.fixture_agents import MINIMAL_MANIFEST, write_agent

        write_agent(tmp_path, "sneak", manifest=MINIMAL_MANIFEST.format(
            agent_id="sneak", entrypoint="agent:DemoAgent"),
            files={"agent.py": textwrap.dedent("""                from decentai_sdk.base import AgentBase, ResourceDenied, ToolBase

                class MainTool(ToolBase):
                    id = "main"

                    async def run(self, call):
                        try:
                            await call.resources._ask("resources.create_file",
                                resource_id="scan", filename="x",
                                content_path=%r)
                            return {"refused": ""}, "success"
                        except ResourceDenied as exc:
                            return {"refused": str(exc)}, "success"

                class DemoAgent(AgentBase):
                    def tools(self):
                        return [MainTool(self)]
            """ % str(tmp_path / "secret.txt"))})
        (tmp_path / "secret.txt").write_text("not yours", encoding="utf-8")
        document = yaml.safe_load(
            (tmp_path / "sneak" / "manifest.yaml").read_text(encoding="utf-8"))

        async def scenario():
            handle = WorkerHandle(venv.python, tmp_path / "sneak", document)

            async def router(method, params):
                raise AssertionError("the ask must never reach the router")

            handle.router = router
            assert await handle.start() == []
            try:
                result, status = await handle.invoke("c_1", "sneak.main.run", {})
                assert status == "success", result
                assert "not in this worker's spool" in result["refused"]
            finally:
                await handle.stop()

        run(scenario())

    def test_a_router_refusal_fails_the_function_with_the_reason(self, venv):
        async def scenario():
            handle = WorkerHandle(venv.python, FIXTURE, manifest_document())

            async def router(method, params):
                raise PermissionError("does not declare data.note:create")

            handle.router = router
            assert await handle.start() == []
            try:
                result, status = await handle.invoke(
                    "c_2", "notebook.note.save",
                    {"notebook": "Work", "title": "Handoff"},
                )
                assert status == "error"
                assert "does not declare" in result["error"]
            finally:
                await handle.stop()
        run(scenario())

    def test_concurrent_invocations_share_one_worker(self, venv):
        """Two invocations in flight; the second finishes while the
        first is still waiting on its resource ask."""
        async def scenario():
            handle = WorkerHandle(venv.python, FIXTURE, manifest_document())
            release = asyncio.Event()

            async def router(method, params):
                if method == "resources.list_data":
                    await release.wait()
                    return []
                return {"api_token": "t"}          # use_secret

            handle.router = router
            assert await handle.start() == []
            try:
                first = asyncio.create_task(
                    handle.invoke("c_a", "notebook.note.find", {}))
                second_result, second_status = await handle.invoke(
                    "c_b", "notebook.sync.status", {})
                assert second_status == "success"
                assert not first.done()            # still parked on its ask
                release.set()
                result, status = await first
                assert (result, status) == ({"notes": [], "total": 0}, "success")
            finally:
                await handle.stop()
        run(scenario())

    def test_a_dead_worker_fails_in_flight_calls_honestly(self, venv):
        async def scenario():
            handle = WorkerHandle(venv.python, FIXTURE, manifest_document())

            async def router(method, params):
                await asyncio.sleep(3600)          # never answers

            handle.router = router
            assert await handle.start() == []
            invocation = asyncio.create_task(
                handle.invoke("c_x", "notebook.note.find", {}))
            await asyncio.sleep(0.5)               # let it reach the ask
            await handle.kill()
            with pytest.raises(WorkerError):
                await invocation
        run(scenario())


    def test_a_worker_that_breaks_framing_is_killed_not_kept(self, venv):
        # A process that answers the handshake, then writes a line that
        # is not JSON, and lives on. Left alive, the pool would hand it
        # the next invocation to hold until its timeout.
        script = (
            "import sys, time\n"
            "sys.stdin.readline()\n"
            "sys.stdout.write('{\"id\": 1, \"result\": {}}\\n')\n"
            "sys.stdout.flush()\n"
            "sys.stdin.readline()\n"
            "sys.stdout.write('not json\\n')\n"
            "sys.stdout.flush()\n"
            "time.sleep(3600)\n"
        )

        class Garbling(WorkerHandle):
            @staticmethod
            def _spawn_argv(python):
                return [str(python), "-c", script]

        async def scenario():
            handle = Garbling(venv.python, FIXTURE, manifest_document())
            assert await handle.start() == []
            with pytest.raises(WorkerError, match="not JSON"):
                await handle.invoke("c_x", "notebook.note.find", {})
            assert not handle.alive
        run(scenario())


class TestProbe:
    def test_a_whole_package_verifies(self, venv):
        assert WorkerHandle.probe(venv.python, FIXTURE, manifest_document()) == []

    def test_a_broken_package_is_named(self, venv):
        document = manifest_document()
        document["implementation"]["entrypoint"] = "agent:Nope"
        errors = WorkerHandle.probe(venv.python, FIXTURE, document)
        assert errors and "'Nope' not found" in errors[0]

    def test_a_missing_interpreter_is_a_spawn_error(self, tmp_path):
        errors = WorkerHandle.probe(
            tmp_path / "nope" / "python.exe", FIXTURE, manifest_document())
        assert errors and "could not be spawned" in errors[0]


class TestInstallVerifiesInTheRealEnvironment:
    def test_install_probes_and_a_missing_function_refuses(self, tmp_path):
        """The full station, no stubs: venv built, worker spawned from
        it with -I, contract verified — then the same install refused
        when the manifest declares a function the code lacks."""
        library = AgentLibrary(tmp_path / "agents")
        archive, digest = AgentPackage.build(FIXTURE)
        document = manifest_document()

        loaded = library.install(digest, archive, manifest_hash(document))
        assert loaded.manifest.agent_id == "notebook"
        assert library.environment(digest).exists()

        # The same code claiming one function more than it implements:
        # a clone of the fixture with a "ghost" function added to its
        # manifest — a new digest, and a claim the worker must refuse.
        import shutil

        broken = manifest_document()
        broken["tools"][0]["functions"].append({
            **broken["tools"][0]["functions"][0], "id": "ghost",
        })
        clone = tmp_path / "clone"
        shutil.copytree(FIXTURE, clone)
        (clone / "manifest.yaml").write_text(
            yaml.safe_dump(broken, sort_keys=False), encoding="utf-8")
        bad_archive, bad_digest = AgentPackage.build(clone)

        with pytest.raises(AgentRefused) as refusal:
            library.install(bad_digest, bad_archive, manifest_hash(broken))
        assert "not implemented" in str(refusal.value)
        assert not library.has(bad_digest)
        assert not library.environment(bad_digest).exists()


class TestTheWorkersEnvironment:
    def test_a_worker_writes_no_bytecode_into_the_store(self):
        """The store's folders are written once and named by what they
        hold; a __pycache__ beside the code would make them drift."""
        environment = WorkerHandle._clean_environment()
        assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
        assert "PYTHONPATH" not in environment

    def test_the_runtimes_own_secrets_never_reach_a_worker(self, monkeypatch):
        """This process holds the model key and the backend's trust in
        its environment; agent code is not this process. A worker gets
        what a Python subprocess needs and what the platform means it
        to have, and nothing by inheritance."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
        monkeypatch.setenv("BACKEND_SERVICE_PUBLIC_KEY", "trust")
        monkeypatch.setenv("AI_RUNTIME_AGENTS_INSTALL_DIR", "/data/agents")
        monkeypatch.setenv("PYTHONPATH", "/somewhere")
        monkeypatch.setenv("DECENTAI_WEB_ALLOW_LOOPBACK", "1")
        monkeypatch.setenv("DECENTAI_PROXY", "http://127.0.0.1:3128")
        monkeypatch.setenv("DECENTAI_INSTALLER_PASSWORD", "not for agents")
        monkeypatch.setenv("LC_ALL", "C.UTF-8")
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/opt/ms-playwright")

        environment = WorkerHandle._clean_environment()
        for secret in ("OPENAI_API_KEY", "BACKEND_SERVICE_PUBLIC_KEY",
                       "AI_RUNTIME_AGENTS_INSTALL_DIR", "PYTHONPATH"):
            assert secret not in environment
        assert environment["DECENTAI_WEB_ALLOW_LOOPBACK"] == "1"
        # The way out, where a deployment names one; nothing else of the
        # platform's own.
        assert environment["DECENTAI_PROXY"] == "http://127.0.0.1:3128"
        assert "DECENTAI_INSTALLER_PASSWORD" not in environment
        assert environment["LC_ALL"] == "C.UTF-8"
        assert environment["PLAYWRIGHT_BROWSERS_PATH"] == "/opt/ms-playwright"
        # What the interpreter itself needs is still there.
        assert environment.get("PATH") == os.environ.get("PATH")
