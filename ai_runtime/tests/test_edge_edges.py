"""The edges themselves: the services client, the model connectors and
the simulation. A failure is said as one, a key goes nowhere it was not
sent, and the simulation refuses what the platform refuses.
"""

import asyncio

import httpx
import pytest

from ai_runtime.execution.resources import ResourceAccess
from ai_runtime.llms.connector.http import ProviderError
from ai_runtime.llms.connector.tools import (
    is_context_overflow, is_image_refusal, setting)
from ai_runtime.services.backend import BackendSchedules, Gateway, GatewayError
from sim.schedules import MemoryScheduleStore
from sim.session_services import SimSessionServices


def run(awaitable):
    return asyncio.run(awaitable)


class TestTheServicesClient:
    def gateway(self, handler):
        gateway = Gateway("http://platform.invalid")
        gateway._client = httpx.AsyncClient(
            base_url="http://platform.invalid",
            transport=httpx.MockTransport(handler))
        return gateway

    def test_a_platform_that_cannot_be_reached_is_a_gateway_error(self):
        def times_out(request):
            raise httpx.ReadTimeout("")
        with pytest.raises(GatewayError) as failed:
            run(self.gateway(times_out).call("AI:Event:Record", {}, "key"))
        assert "ReadTimeout" in str(failed.value)

    def test_an_error_with_no_message_names_what_failed(self):
        def answers(request):
            return httpx.Response(200, json={
                "status": "error", "request_id": "r", "error": {}})
        with pytest.raises(GatewayError, match="AI:State:Save failed"):
            run(self.gateway(answers).call("AI:State:Save", {}, "key"))

    def test_a_refused_write_drops_only_the_key_it_was_sent_with(self):
        class Services:
            def __init__(self):
                self.credentials = {"chat_1": "old"}
                self.revoked = []

            async def call(self, chat_id, endpoint, data=None):
                # Renewed while the write was on its way.
                self.credentials[chat_id] = "new"
                raise GatewayError("refused", 401)

            def revoke(self, chat_id):
                self.revoked.append(chat_id)
                self.credentials.pop(chat_id, None)

        services = Services()
        with pytest.raises(GatewayError):
            run(BackendSchedules(services).ran(
                {"chat_id": "chat_1", "schedule_id": "a"}))
        assert services.revoked == [] and services.credentials == {
            "chat_1": "new"}


class TestAConnectionsNumbers:
    def test_no_retries_is_none(self):
        assert setting({"max_retries": 0}, "max_retries", 2, 0, 10) == 0

    def test_a_timeout_of_nothing_is_the_default(self):
        assert setting({"timeout_seconds": 0}, "timeout_seconds", 60, 1, 600) == 60

    @pytest.mark.parametrize("said, kept", [
        (None, 2), ("x", 2), (-3, 0), (500, 10), (True, 2)])
    def test_they_are_held_to_what_makes_sense(self, said, kept):
        assert setting({"max_retries": said}, "max_retries", 2, 0, 10) == kept


class TestReadingARefusal:
    def test_a_limit_that_names_a_vision_model_is_not_about_pictures(self):
        assert not is_image_refusal(RuntimeError(
            "429: rate limit reached for gpt-4-vision-preview"))
        assert is_image_refusal(RuntimeError(
            "400: this model does not support image input"))

    def test_an_empty_account_is_not_a_long_transcript(self):
        assert not is_context_overflow(RuntimeError(
            "You exceeded your monthly token limit; check your billing"))
        assert is_context_overflow(RuntimeError("context_length_exceeded"))

    def test_a_provider_error_carries_its_status(self):
        assert ProviderError("answered 404", 404).status_code == 404


class TestAFilesRowIsOneShape:
    def test_whoever_keeps_the_files(self):
        platform = {"resource_ref": "fil_1", "keys": {"category": "document"},
                    "values": {"filename": "a.csv", "file_size": 12}}
        simulated = {"resource_ref": "fil_1", "resource_id": "document",
                     "filename": "a.csv"}
        for row in (platform, simulated):
            found = ResourceAccess._file_row(row)
            assert (found["resource_ref"], found["filename"],
                    found["file_type"]) == ("fil_1", "a.csv", "text/csv")

    def test_a_read_names_the_file_it_read(self):
        found = ResourceAccess._file_row(
            ResourceAccess._readable({"filename": "a.txt", "content": b"hi"}),
            "fil_9")
        assert found["resource_ref"] == "fil_9" and found["file_size"] == 2
        assert found["content"] == "hi"


class TestTheSimulationRefusesWhatThePlatformRefuses:
    def test_an_actor_it_does_not_know(self):
        with pytest.raises(ValueError):
            run(SimSessionServices().persist_message(
                "chat_1", "somebody", "hello", []))

    def test_a_message_of_nothing(self):
        with pytest.raises(ValueError):
            run(SimSessionServices().persist_message("chat_1", "user", "", []))

    def test_a_part_outside_the_contract(self):
        with pytest.raises(ValueError):
            run(SimSessionServices().persist_message(
                "chat_1", "ai", "see", [{"type": "nonsense"}]))

    def test_a_state_over_its_size(self):
        with pytest.raises(ValueError):
            run(SimSessionServices().save_state(
                "chat_1", {"messages": ["x" * 600_000]}))

    def test_a_saved_state_is_a_copy(self):
        services, state = SimSessionServices(), {"messages": ["a"]}
        run(services.save_state("chat_1", state))
        state["messages"].append("not saved")
        assert services.states["chat_1"] == {"messages": ["a"]}

    def test_an_event_over_its_size(self):
        with pytest.raises(ValueError):
            run(SimSessionServices().record_event(
                "chat_1", {"event": "wakeup", "result": "x" * 20_000}))

    def test_history_is_the_last_hundred(self):
        async def scenario():
            services = SimSessionServices()
            for number in range(120):
                await services.persist_message(
                    "chat_1", "user", f"m{number}", [])
            return await services.history("chat_1")
        assert len(run(scenario())) == 100

    def test_an_answer_after_a_card_expired(self):
        async def scenario():
            services = SimSessionServices()
            card = await services.open_approval("chat_1", {"kind": "question"})
            await services.expire_approval("chat_1", card)
            return (await services.resolve_answer(card, "late"),
                    await services.resolve_approval(card, True))
        assert run(scenario()) == (False, False)

    def test_a_fifty_first_schedule_and_another_chats_row(self):
        async def scenario():
            store = MemoryScheduleStore()
            for number in range(50):
                await store.add({"chat_id": "chat_1",
                                 "schedule_id": f"s{number}"})
            with pytest.raises(ValueError):
                await store.add({"chat_id": "chat_1", "schedule_id": "s50"})
            await store.remove("chat_2", "s1")
            return len(store.rows)
        assert run(scenario()) == 50


class TestAKeyGoesNowhereItWasNotSent:
    @pytest.mark.parametrize("provider", ["anthropic", "openai"])
    def test_a_redirect_is_not_followed(self, provider):
        from ai_runtime.llms.factory import LLMConnectorFactory
        connector = LLMConnectorFactory.create({
            "provider": provider, "api_key": "test-key",
            "model": "chosen-model"})
        assert connector.client._client.follow_redirects is False


class TestTheSimulationKeepsAFilesSizeAndType:
    def test_on_the_row_it_answers_with_and_the_rows_it_lists(self):
        from sim.resources import InMemoryResourceProvider

        async def scenario():
            provider = InMemoryResourceProvider()
            made = await provider.create_file("document", "a.csv", "x,y")
            [listed] = await provider.list_files("document")
            return made, listed
        for row in run(scenario()):
            assert (row["filename"], row["file_type"], row["file_size"]) == (
                "a.csv", "text/csv", 3)


class TestAPreviewSaysWhereTheWholeIs:
    def test_only_where_it_was_kept(self):
        from ai_runtime.reasoning.observations import preview_note
        assert "is stored" in preview_note({}, stored=True)
        unkept = preview_note({}, stored=False)
        assert "is stored" not in unkept and "was not kept" in unkept


class TestWhereAKeyIsSentIsTheConnectionsToSay:
    @pytest.mark.parametrize("provider, variable, address", [
        ("openai", "OPENAI_BASE_URL", "https://api.openai.com/v1"),
        ("anthropic", "ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
    ])
    def test_an_address_in_the_environment_is_not_read(
            self, monkeypatch, provider, variable, address):
        """The client libraries would read one for a client given no
        address. Every client is given the catalog's."""
        from ai_runtime.llms.factory import LLMConnectorFactory
        monkeypatch.setenv(variable, "http://elsewhere.invalid/v1")
        connector = LLMConnectorFactory.create({
            "provider": provider, "api_key": "test-key",
            "model": "chosen-model"})
        assert str(connector.client.base_url).rstrip("/") == address
