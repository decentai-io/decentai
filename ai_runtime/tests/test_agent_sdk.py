"""The agents-layer SDK, driven by the Notebook reference manifest.

A skeleton NotebookAgent proves the contract: manifest-injected identity,
tool-class composition, declared-only function resolution, the FunctionCall
argument, and lifecycle teardown.
"""

import asyncio
from pathlib import Path

import pytest

from contracts.agent_manifest import load_manifest
from decentai_sdk.base import AgentBase, FunctionCall, ToolBase

MANIFEST_PATH = (
    Path(__file__).resolve().parent
    / "fixtures" / "agents" / "notebook" / "manifest.yaml"
)


@pytest.fixture(scope="module")
def manifest():
    loaded, errors = load_manifest(MANIFEST_PATH)
    assert errors == []
    return loaded


# A minimal skeleton implementation — enough to exercise the SDK contract.

class NoteTool(ToolBase):
    id = "note"

    async def save(self, call):
        await call.progress("saving")
        return {"note_ref": "data_1", "created": True}, "success"

    async def find(self, call):
        return {"notes": [], "total": 0}, "success"

    async def steal(self, call):
        # Exists in code, NOT in the manifest — must be unreachable.
        return {"stolen": True}, "success"


class ArchiveTool(ToolBase):
    id = "archive"

    async def export(self, call):
        return {"file_ref": "file_1", "note_count": 0}, "success"

    def import_(self, call):
        # The manifest id "import" is a Python keyword; the SDK resolves
        # the PEP 8 trailing-underscore spelling.
        return {"note_refs": [], "imported": 0}, "success"


class SyncTool(ToolBase):
    id = "sync"

    def __init__(self, agent):
        super().__init__(agent)
        self.closed = False

    async def status(self, call):
        return {"connected": True, "remote": "simulated"}, "success"

    async def push(self, call):
        return {"pushed": 0, "digest": "deadbeef"}, "success"

    async def close(self):
        self.closed = True


class NotebookAgent(AgentBase):
    def tools(self):
        return [NoteTool(self), ArchiveTool(self), SyncTool(self)]


class TestResolution:
    def test_all_manifest_functions_resolve(self, manifest):
        agent = NotebookAgent(manifest)
        assert agent.missing_functions() == []
        for name, _, _ in manifest.functions():
            assert callable(agent.function(name)), name

    def test_agent_identity_comes_from_the_manifest(self, manifest):
        agent = NotebookAgent(manifest)
        assert agent.agent_id == "notebook"

    def test_undeclared_method_is_unreachable(self, manifest):
        agent = NotebookAgent(manifest)
        assert agent.function("notebook.note.steal") is None

    def test_unknown_canonical_name_is_none(self, manifest):
        agent = NotebookAgent(manifest)
        assert agent.function("notebook.note.nope") is None
        assert agent.function("other.note.save") is None

    def test_missing_implementation_is_reported(self, manifest):
        class ForgetfulNoteTool(NoteTool):
            find = None  # shadow the implementation away

        class ForgetfulAgent(NotebookAgent):
            def tools(self):
                return [ForgetfulNoteTool(self), ArchiveTool(self), SyncTool(self)]

        agent = ForgetfulAgent(manifest)
        assert agent.missing_functions() == ["notebook.note.find"]
        assert agent.function("notebook.note.find") is None

    def test_duplicate_tool_ids_rejected(self, manifest):
        class DoubledAgent(NotebookAgent):
            def tools(self):
                return [NoteTool(self), NoteTool(self)]

        with pytest.raises(ValueError, match="Duplicate tool id"):
            DoubledAgent(manifest)

    def test_tool_without_id_rejected(self, manifest):
        class AnonymousTool(ToolBase):
            pass

        class AnonymousAgent(NotebookAgent):
            def tools(self):
                return [AnonymousTool(self)]

        with pytest.raises(ValueError, match="declares no tool id"):
            AnonymousAgent(manifest)


class TestInvocation:
    def test_function_call_carries_inputs_and_progress(self, manifest):
        agent = NotebookAgent(manifest)
        seen = []

        async def sink(description):
            seen.append(description)

        async def scenario():
            save = agent.function("notebook.note.save")
            call = FunctionCall(
                {"notebook": "personal", "title": "Hi"}, progress_sink=sink
            )
            return await save(call)

        result, status = asyncio.run(scenario())
        assert status == "success"
        assert result["created"] is True
        assert seen == ["saving"]

    def test_progress_without_sink_is_a_noop(self):
        call = FunctionCall({"a": 1})
        asyncio.run(call.progress("nothing listens"))
        assert call.inputs == {"a": 1}

    def test_sync_functions_resolve_too(self, manifest):
        agent = NotebookAgent(manifest)
        import_fn = agent.function("notebook.archive.import")
        result, status = import_fn(FunctionCall({"notebook": "p", "file_ref": "f"}))
        assert status == "success"
        assert result["imported"] == 0

    def test_close_cascades_to_tools(self, manifest):
        agent = NotebookAgent(manifest)
        asyncio.run(agent.close())
        sync_tool = agent._tools["sync"]
        assert sync_tool.closed is True
