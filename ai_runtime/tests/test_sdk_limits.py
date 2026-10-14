"""The SDK is dependency-free, so it keeps its own copies of the
contract's screen limits (decentai_sdk/base.py, Screen). This holds the
copies to the contract, which is the only thing that can."""

from contracts import chat
from decentai_sdk.base import Screen
from decentai_sdk import worker as sdk_worker
from ai_runtime.agents.workers import WorkerHandle


def test_the_sdk_screen_limits_are_the_contracts():
    assert Screen.MAX_BYTES == chat.SCREEN_FRAME_MAX_BYTES
    assert Screen.TABS_MAX == chat.SCREEN_TABS_MAX
    assert Screen.TAB_INDEX_MAX == chat.SCREEN_TAB_INDEX_MAX
    assert Screen.TAB_TITLE_CHARS == chat.SCREEN_TAB_TITLE_MAX_CHARS
    assert Screen.TAB_ADDRESS_CHARS == chat.SCREEN_TAB_ADDRESS_MAX_CHARS


def test_the_worker_protocol_constants_are_the_runtimes():
    assert sdk_worker.PROTOCOL_VERSION == WorkerHandle.PROTOCOL_VERSION
    assert sdk_worker.WireResources.INLINE_LIMIT == WorkerHandle.INLINE_LIMIT
