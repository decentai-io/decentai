"""The edges of the shared contracts and the SDK: what is malformed is
refused in words, and never raised at whoever asked.

Each case here was a way to make one of them raise, hang or answer
wrongly; they are kept so that it stays the refusal it now is.
"""

import asyncio
import copy
import datetime
import io
import socket
import tarfile
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from contracts.agent_manifest import ManifestValidator, manifest_hash
from contracts.agent_package import AgentPackage, PackagingError
from contracts.agent_samples import SampleSheet
from contracts.chat import display_stored
from contracts.cron import Cron
from contracts.file_types import FileTypes
from contracts.mcp import McpClient, McpError
from contracts.protocol import EndpointResponse
from contracts.record_fields import RecordFields
from decentai_sdk.base import AgentBase, FunctionCall, Screen, ToolBase
from decentai_sdk.manifest import Manifest
from decentai_sdk.net import Tunnel

NOTE = Path(__file__).resolve().parents[2] / "examples" / "note" / "manifest.yaml"
LONDON = ZoneInfo("Europe/London")


def note() -> dict:
    return yaml.safe_load(NOTE.read_text(encoding="utf-8"))


# ── cron ─────────────────────────────────────────────────────────────

class TestCronLists:
    def test_a_plain_value_beside_a_stepped_one_is_only_itself(self):
        assert Cron("0,*/20 * * * *").minutes == {0, 20, 40}
        assert Cron("0 9,*/6 * * *").hours == {0, 6, 9, 12, 18}

    def test_a_value_with_a_step_still_runs_to_the_end(self):
        assert Cron("5/15 * * * *").minutes == {5, 20, 35, 50}


class TestCronWhenClocksGoBack:
    """25 October 2026, London: 01:00 to 02:00 comes round twice."""

    def at(self, minute: int, fold: int) -> float:
        return datetime.datetime(2026, 10, 25, 1, minute, tzinfo=LONDON,
                                 fold=fold).timestamp()

    @pytest.mark.parametrize("fold", [0, 1])
    def test_the_next_run_is_after_the_moment_asked_from(self, fold):
        asked = self.at(10, fold)
        assert Cron("*/5 * * * *").next_after(asked, LONDON) == asked + 300

    def test_it_only_ever_goes_forward(self):
        cron, moment = Cron("*/5 * * * *"), self.at(0, 0) - 3600
        for _ in range(60):
            following = cron.next_after(moment, LONDON)
            assert following > moment
            moment = following


# ── the manifest validator ───────────────────────────────────────────

def _data(document):
    return document["resources"]["data"][0]


def _function(document):
    return document["tools"][0]["functions"][0]


def _input(document, schema):
    _function(document)["inputs"]["properties"]["extra"] = schema


MALFORMED = {
    "a list as a user_access operation":
        lambda d: _data(d).__setitem__("user_access", [["create"]]),
    "a list as a cardinality":
        lambda d: _data(d)["binding"].__setitem__("cardinality", ["one"]),
    "a list as a field's type":
        lambda d: _data(d)["fields"][0].__setitem__("type", ["string"]),
    "a mapping as a field's storage":
        lambda d: _data(d)["fields"][0].__setitem__("storage", {"a": 1}),
    "a list as a permission level":
        lambda d: _function(d).__setitem__("permission_level", [1]),
    "a list inside a schema's type":
        lambda d: _input(d, {"type": [["string"]]}),
    "a list as an x-resource type":
        lambda d: _input(d, {"type": "string",
                             "x-resource": {"type": ["data"], "id": "note"}}),
    "a list as an x-resource id":
        lambda d: _input(d, {"type": "string",
                             "x-resource": {"type": "data", "id": ["note"]}}),
    "a list as a tool's resource id":
        lambda d: d["tools"][0].__setitem__("resources", {"data": [["note"]]}),
    "a mapping as an operation":
        lambda d: _function(d).__setitem__(
            "resources", {"data": {"note": [{"a": 1}]}}),
    "a list as a normalization":
        lambda d: d.setdefault("authorization", {}).setdefault(
            "scopes", {}).__setitem__(
                "extra", {"type": "string", "description": "x",
                          "normalization": ["lowercase"]}),
    "a number as a schema keyword":
        lambda d: _function(d)["inputs"].__setitem__(1, "x"),
    "unknown resource kinds of two types":
        lambda d: d["resources"].update({1: [], "zzz": []}),
    "unknown network keys of two types":
        lambda d: d["network"].update({1: [], "zzz": []}),
    "a date where JSON has none":
        lambda d: d["agent"].__setitem__("released", datetime.date(2024, 1, 1)),
    "minLength that is not a count":
        lambda d: _input(d, {"type": "string", "minLength": "abc"}),
    "enum that is not a list":
        lambda d: _input(d, {"type": "string", "enum": "notalist"}),
    "a pattern that is not one":
        lambda d: _input(d, {"type": "string", "pattern": "("}),
    "a function named for what every tool has":
        lambda d: _function(d).__setitem__("id", "close"),
}


class TestTheValidatorAnswersInWords:
    def test_the_example_is_valid_and_hashes(self):
        assert ManifestValidator().validate(note()) == []
        assert manifest_hash(note())

    @pytest.mark.parametrize("what", sorted(MALFORMED))
    def test_it_refuses_and_does_not_raise(self, what):
        document = copy.deepcopy(note())
        MALFORMED[what](document)
        errors = ManifestValidator().validate(document)
        assert errors and all(isinstance(error, str) for error in errors)


# ── the package ──────────────────────────────────────────────────────

def _archive(entries) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tar:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return raw.getvalue()


class TestAPackageThatIsNotOne:
    @pytest.mark.parametrize("archive", [
        _archive([(".", b"x")]),
        _archive([("a", b"x"), ("a/b", b"y")]),
        _archive([("a", b"x" * 5000)])[:-40],
    ], ids=["an entry named dot", "an entry beneath a file", "cut short"])
    def test_it_is_refused_as_a_package(self, archive, tmp_path):
        with pytest.raises(PackagingError):
            AgentPackage.extract(archive, tmp_path / "agent")

    def test_a_sound_one_unpacks(self, tmp_path):
        AgentPackage.extract(_archive([("a/b.py", b"x")]), tmp_path / "agent")
        assert (tmp_path / "agent" / "a" / "b.py").read_bytes() == b"x"


# ── samples and record fields ────────────────────────────────────────

SHAPE = {"resources": {"data": [{"id": "note", "fields": [
    {"name": "title", "type": "string", "required": True},
    {"name": "count", "type": "number"},
    {"name": "kind", "type": "select", "options": ["a", "b"]},
    {"name": "about", "type": "string"},
]}], "files": [{"id": "doc"}]}}
DECLARED = RecordFields.declared(SHAPE["resources"]["data"][0])


def _loads(fields: dict) -> bool:
    try:
        RecordFields.split(DECLARED, fields)
    except ValueError:
        return False
    return True


class TestASampleSheet:
    @pytest.mark.parametrize("manifest", [
        [], {"resources": []}, {"resources": {"data": 5, "files": "x"}},
        {"resources": {"data": [{"id": "note", "fields": 7}]}},
    ])
    def test_a_manifest_not_yet_validated_does_not_break_the_reader(
            self, manifest):
        sheet = SampleSheet.parse(
            {"story": "x", 1: 2, "zz": 3,
             "records": [{"slot": "note", "fields": {1: "a", "b": 2}}]},
            manifest)
        assert sheet.errors

    @pytest.mark.parametrize("fields", [
        {"title": "t", "count": "@one"},
        {"title": "t", "kind": "@one"},
        {"title": "t", "about": None},
        {"title": "t", "kind": " a "},
        {"title": "t", "about": "@one"},
        {"title": "t", "count": 3, "kind": "b"},
        {"title": "  "},
        {"title": "t", "count": "x"},
    ])
    def test_what_reads_clean_is_what_loads(self, fields):
        sheet = SampleSheet.parse({"records": [
            {"ref": "one", "slot": "note", "fields": {"title": "t"}},
            {"slot": "note", "fields": fields},
        ]}, SHAPE)
        kept = sheet.records[-1]["fields"]
        as_loaded = {name: "an-id" if str(value).startswith("@") else value
                     for name, value in kept.items()}
        assert (not sheet.errors) == _loads(as_loaded)


class TestAnUpdate:
    def test_it_may_not_empty_a_required_field(self):
        with pytest.raises(ValueError):
            RecordFields.split(DECLARED, {"title": ""}, partial=True)
        with pytest.raises(ValueError):
            RecordFields.split(DECLARED, {"title": None}, partial=True,
                               nothing_is_nothing=True)

    def test_it_may_leave_a_required_field_out(self):
        assert RecordFields.split(DECLARED, {"count": 4}, partial=True)

    def test_an_agent_may_still_clear_an_optional_one(self):
        keys, _ = RecordFields.split(DECLARED, {"about": None}, partial=True,
                                     nothing_is_nothing=True)
        assert keys == {"about": None}


# ── smaller shapes ───────────────────────────────────────────────────

class TestSmallerThings:
    def test_a_packed_file_is_what_it_is_packed_as(self):
        assert FileTypes.of("notes.tar.gz") == "application/gzip"
        assert FileTypes.of("notes.csv") == "text/csv"

    @pytest.mark.parametrize("message", ["", None, "x" * 5000])
    def test_a_failure_can_always_be_built(self, message):
        error = EndpointResponse.failure("r1", "refused", message).error
        assert 1 <= len(error.message) <= 2048

    def test_a_display_holding_a_number_that_is_not_one_is_refused(self):
        stored, why = display_stored(
            {"kind": "table", "title": "t", "rows": [{"a": float("nan")}]})
        assert stored is None and "NaN" in why


class TestAnMcpServerThatAnswersOddly:
    def run(self, coroutine):
        return asyncio.run(coroutine)

    @pytest.mark.parametrize("address", [
        "https://example.com:99999/x", "https://a..b.example/x"])
    def test_an_address_that_is_not_one_is_an_mcp_error(self, address):
        async def opening():
            McpClient.resolver = None
            await McpClient(address, {}).open()
        with pytest.raises(McpError):
            self.run(opening())

    def test_content_of_the_wrong_shape_is_no_content(self):
        client = McpClient("https://example.com/x", {})

        async def answers(method, params):
            return {"content": 5, "contents": "x",
                    "tools": [{"name": "a" * 121}, {"name": "ok"}]}
        client.request = answers
        client.capabilities = {"tools": {}}
        assert self.run(client.call("t", {}))["text"] == ""
        assert self.run(client.read("u"))["text"] == ""
        # A name is what a call sends back: kept whole, or left out.
        assert [tool["name"] for tool in self.run(client.tools())] == ["ok"]

    def test_a_resource_that_is_text_is_passed_over(self):
        client = McpClient("https://example.com/x", {})

        async def answers(method, params):
            return {"content": [{"type": "resource", "resource": "words"}]}
        client.request = answers
        assert self.run(client.call("t", {}))["other"] == ["resource"]


# ── the SDK ──────────────────────────────────────────────────────────

class TestTheSdk:
    def test_a_host_the_proxy_cannot_be_asked_for_is_an_oserror(self):
        listening = socket.socket()
        listening.bind(("127.0.0.1", 0))
        listening.listen(1)
        port = listening.getsockname()[1]
        try:
            with pytest.raises(OSError):
                Tunnel.through(f"http://u:p@127.0.0.1:{port}",
                               "ex中ample.com", 443, 2)
        finally:
            listening.close()

    def test_what_every_tool_inherits_is_not_a_function(self):
        document = note()
        document["tools"] = [document["tools"][0]]
        document["tools"][0]["functions"] = [
            dict(document["tools"][0]["functions"][0], id="close")]

        class Tool(ToolBase):
            id = document["tools"][0]["id"]

        class Agent(AgentBase):
            def tools(self):
                return [Tool(self)]

        assert len(Agent(Manifest(document)).missing_functions()) == 1

    def test_a_picture_that_is_not_named_is_said_to_the_function(self):
        async def model(messages, max_tokens=None, pictures=None):
            return "seen"

        async def asking():
            await FunctionCall({}, llm=model).llm("what is this", images=[b"bytes"])
        with pytest.raises(ValueError):
            asyncio.run(asking())

    def test_a_tab_keeps_to_the_place_the_contract_allows(self):
        assert Screen._tabs([{"index": 5000}])[0]["index"] == 1000


# ── the worker loop ──────────────────────────────────────────────────

class _Wire:
    """Stands where the pipe is, and serializes as the pipe's end does."""

    def __init__(self):
        self.sent = []

    def send(self, message):
        import json
        json.dumps(message)
        self.sent.append(message)


class TestTheWorkerLoop:
    def worker(self):
        from decentai_sdk.worker import AgentWorker
        return AgentWorker(_Wire())

    def answer(self, method):
        worker = self.worker()
        asyncio.run(worker._run_function(7, "c1", "note.notes.get", method,
                                         {"inputs": {}}))
        return worker.wire.sent[-1]

    def test_a_result_json_cannot_say_is_still_answered(self):
        async def function(call):
            return {"when": datetime.date(2026, 1, 1)}, "success"
        sent = self.answer(function)
        assert sent["id"] == 7 and sent["result"]["status"] == "error"
        assert "not JSON" in sent["result"]["result"]["error"]

    def test_a_function_written_without_async_answers_as_any_other(self):
        def function(call):
            return {"ok": True}, "success"
        assert self.answer(function)["result"] == {
            "result": {"ok": True}, "status": "success"}

    def test_a_greeting_with_no_manifest_is_refused_in_words(self, tmp_path):
        worker = self.worker()
        worker._dispatch({"id": 1, "method": "hello", "params": {
            "protocol_version": 1, "folder": str(tmp_path), "manifest": {}}})
        assert "manifest" in worker.wire.sent[-1]["error"]["message"]

    @pytest.mark.parametrize("message", [
        {"id": 1, "method": "invoke", "params": "abc"},
        {"method": "screen.input", "params": "abc"},
        ["not", "a", "message"],
    ])
    def test_a_request_of_the_wrong_shape_does_not_end_the_loop(self, message):
        self.worker()._dispatch(message)
