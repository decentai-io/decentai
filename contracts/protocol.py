"""The envelope every AI-domain endpoint answers in: success, accepted or
error, with the contract's version, so the runtime reads one shape."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from contracts.version import CONTRACT_VERSION


class OperationError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2048)


class EndpointResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    request_id: str
    status: Literal["success", "accepted", "error"]
    data: dict[str, Any] = Field(default_factory=dict)
    error: OperationError | None = None

    @classmethod
    def success(cls, request_id: str, data: dict | None = None, *, accepted: bool = False):
        return cls(
            request_id=request_id,
            status="accepted" if accepted else "success",
            data=data or {},
        )

    @classmethod
    def failure(cls, request_id: str, code: str, message: str):
        return cls(
            request_id=request_id,
            status="error",
            error=OperationError(code=code, message=message),
        )
