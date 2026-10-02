from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Optional, TYPE_CHECKING, cast


if TYPE_CHECKING:
    from database import MongoDB
    from server.authentication.access import AccessController
    from api import APIRouter
    from api.services.chat_session.relay import RuntimeClientManager
    from server.ws.ws_manager import WSManager
    from server.setup.app_settings import Settings


@dataclass
class AppState:
    settings: Optional["Settings"] = None
    db: Optional["MongoDB"] = None
    access_controller: Optional["AccessController"] = None
    api_router: Optional["APIRouter"] = None
    ws_manager: Optional["WSManager"] = None
    runtime_clients: Optional["RuntimeClientManager"] = None


_state = AppState()


def get_state() -> AppState:
    return _state


def reset_state() -> None:
    for field in fields(_state):
        setattr(_state, field.name, None)


def _require(value, name: str):
    if value is None:
        raise RuntimeError(f"{name} not initialized.")
    return value


def get_settings() -> "Settings":
    return cast("Settings", _require(_state.settings, "Settings"))


def get_db() -> "MongoDB":
    return cast("MongoDB", _require(_state.db, "DB"))


def get_access_controller() -> "AccessController":
    return cast("AccessController", _require(_state.access_controller, "AccessController"))


def get_api_router() -> "APIRouter":
    return cast("APIRouter", _require(_state.api_router, "Router"))


def get_ws_manager() -> "WSManager":
    return cast("WSManager", _require(_state.ws_manager, "WS Manager"))


def get_runtime_clients() -> "RuntimeClientManager":
    return cast(
        "RuntimeClientManager",
        _require(_state.runtime_clients, "Runtime Client Manager"),
    )

