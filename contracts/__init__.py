"""Versioned contracts shared by the DecentAI backend and runtime."""

from contracts.protocol import EndpointResponse, OperationError
from contracts.version import CONTRACT_VERSION
from contracts.chat import (
    CHAT_PROTOCOL_VERSION, ChatEvent, ChatInputCommand,
    FilePart, MessagePart, Source, agent_source, event_error,
    part_error,
)

__all__ = [
    "CONTRACT_VERSION",
    "EndpointResponse",
    "OperationError",
    "CHAT_PROTOCOL_VERSION",
    "ChatEvent",
    "ChatInputCommand",
    "FilePart",
    "MessagePart",
    "Source",
    "agent_source",
    "event_error",
    "part_error",
]
