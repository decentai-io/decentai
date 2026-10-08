"""The envelope every AI-domain endpoint answers in: success or
error, with the contract's version, so the runtime reads one shape."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from contracts.version import CONTRACT_VERSION


#: The longest message an error carries.
MESSAGE_MAX = 2048


class OperationError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=MESSAGE_MAX)


class EndpointResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    request_id: str
    status: Literal["success", "error"]
    data: dict[str, Any] = Field(default_factory=dict)
    error: OperationError | None = None

    @classmethod
    def success(cls, request_id: str, data: dict | None = None):
        return cls(
            request_id=request_id,
            status="success",
            data=data or {},
        )

    @classmethod
    def failure(cls, request_id: str, code: str, message: str):
        # Callers pass an exception's own words. One with none, or with
        # more than the envelope carries, is still the failure it was:
        # the message is fitted, and building the error never fails.
        message = str(message or "").strip() or str(code)
        if len(message) > MESSAGE_MAX:
            message = message[:MESSAGE_MAX - 1] + "…"
        return cls(
            request_id=request_id,
            status="error",
            error=OperationError(code=code, message=message),
        )
